"""`make eval`: run the full simulator scenario set through detector → agent N times (default 3).

Each run gets a fresh, isolated compose project (`ledgerguard-eval`): ledger, Kafka, detector, MCP server
and agent worker. The simulator drives the ledger; when every case is investigated and the detector has
gone quiet, cases + reports + agent runs are read from Postgres and scored against the simulator's labels.

Writes eval/results/<timestamp>.json and .md (real LLM) or eval/runs/fake/ (fake LLM smoke test).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE.parent / "simulator"))

from ledgerguard_eval.metrics import aggregate, run_metrics  # noqa: E402
from ledgerguard_eval.report import render  # noqa: E402
from simulator.injection import PUSH_CLEAR, PUSH_FREEZE  # noqa: E402
from stack import ROOT, Stack, log, timestamp, wait_http  # noqa: E402

PROJECT = "ledgerguard-eval"
PORTS = {"POSTGRES_PORT": 55433, "KAFKA_EXTERNAL_PORT": 59095, "LEDGER_PORT": 58081}
SERVICES = ["postgres", "kafka", "ledger-service", "detector", "mcp-server", "agent-worker"]

CASES_SQL = """
select c.id::text as case_id, c.account_id::text as account_id, c.status, c.flags,
       r.verdict, r.risk_score, r.confidence, r.injection_detected, r.low_quality,
       coalesce(a.runs, 0) as runs, coalesce(a.failed, 0) as failed,
       coalesce(a.tool_calls, 0) as tool_calls, coalesce(a.input_tokens, 0) as input_tokens,
       coalesce(a.output_tokens, 0) as output_tokens, coalesce(a.cost_micro_usd, 0) as cost_micro_usd,
       coalesce(a.latency_ms, 0) as latency_ms,
       (select array_agg(h.rule order by h.rule) from case_rule_hits h where h.case_id = c.id) as rules
  from cases c
  left join lateral (select * from reports r where r.case_id = c.id order by r.created_at desc limit 1) r on true
  left join lateral (select count(*) as runs, count(*) filter (where status = 'FAILED') as failed,
                            sum(tool_calls)::bigint as tool_calls, sum(input_tokens)::bigint as input_tokens,
                            sum(output_tokens)::bigint as output_tokens,
                            sum(cost_micro_usd)::bigint as cost_micro_usd, sum(latency_ms)::bigint as latency_ms
                       from agent_runs a where a.case_id = c.id and a.status <> 'RUNNING') a on true
"""

PROGRESS_SQL = """
select (select count(*) from processed_events) as events,
       (select count(*) from cases) as cases,
       (select count(*) from cases where status in ('OPEN', 'INVESTIGATING')) as pending,
       (select count(*) from agent_runs where status = 'RUNNING') as running,
       (select count(*) from agent_runs where status = 'SUCCEEDED') as succeeded,
       (select count(*) from agent_runs where status = 'FAILED') as failed
"""


def sql_text_array(values: list[str]) -> str:
    return "array[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]::text[]"


def exposed_accounts(stack: Stack) -> set[str]:
    rows = stack.rows("ledger", f"""
        select distinct x::text as account_id
          from transfers t, unnest(array[t.from_account_id, t.to_account_id]) x
         where t.memo = any({sql_text_array(PUSH_CLEAR + PUSH_FREEZE)})""")
    return {r["account_id"] for r in rows}


def wait_settled(stack: Stack, timeout_s: float, quiet_polls: int = 3, every_s: float = 10) -> dict:
    """Done when no case is OPEN/INVESTIGATING, no agent run is RUNNING, and the detector's processed
    event count hasn't moved for `quiet_polls` consecutive polls."""
    deadline = time.monotonic() + timeout_s
    last_events, quiet = -1, 0
    while True:
        p = stack.row("agent", PROGRESS_SQL)
        log(f"  events={p['events']} cases={p['cases']} pending={p['pending']} running={p['running']} "
            f"ok={p['succeeded']} failed={p['failed']}")
        if p["failed"] >= 5 and p["succeeded"] == 0:
            err = stack.psql("agent", "select error from agent_runs where status = 'FAILED' "
                                      "order by finished_at desc limit 1")
            raise SystemExit(f"the first {p['failed']} agent runs all failed; last error: {err[:500]}\n"
                             "check LLM_MODEL / ANTHROPIC_API_KEY in .env (and `docker compose -p "
                             f"{PROJECT} logs agent-worker`)")
        quiet = quiet + 1 if p["events"] == last_events and p["events"] > 0 else 0
        last_events = p["events"]
        if p["pending"] == 0 and p["running"] == 0 and quiet >= quiet_polls:
            return p
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out after {timeout_s:.0f}s waiting for investigations to finish")
        time.sleep(every_s)


def run_once(stack: Stack, i: int, args, work_dir: Path) -> dict:
    log(f"=== run {i} of {args.runs} ===")
    stack.down()
    stack.up(*SERVICES)
    ledger_url = f"http://localhost:{PORTS['LEDGER_PORT']}"
    wait_http(f"{ledger_url}/actuator/health")
    labels_path = work_dir / f"labels-run{i}.jsonl"
    run_id = f"eval{work_dir.name}r{i}"
    t0 = time.monotonic()
    log(f"simulating (seed {args.seed})")
    sim = subprocess.run(
        ["uv", "run", "python", "-m", "simulator", "--seed", str(args.seed), "--ledger-url", ledger_url,
         "--labels-out", str(labels_path), "--run-id", run_id],
        cwd=ROOT / "simulator", check=True, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "LEDGER_CLIENT_API_KEY": stack.env["LEDGER_CLIENT_API_KEY"],
             "SIM_TIME_SCALE": stack.env.get("SIM_TIME_SCALE", "3600")})
    sim_summary = json.loads(sim.stdout)
    log(f"simulation done in {time.monotonic() - t0:.0f}s; waiting for detector + agent")
    progress = wait_settled(stack, args.timeout)
    cases = stack.rows("agent", CASES_SQL)
    labels = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line]
    exposed = exposed_accounts(stack)
    metrics = run_metrics(cases, labels, exposed)
    (work_dir / f"cases-run{i}.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")
    log(f"run {i}: {len(cases)} cases, precision={metrics['precision']}, recall={metrics['recall']}, "
        f"auto_clear={metrics['auto_clear_rate']}, leak={metrics['fraud_leak_rate_auto_cleared']}")
    return {"run": i, "run_id": run_id, "wall_seconds": round(time.monotonic() - t0),
            "simulator": {k: sim_summary.get(k) for k in ("accounts", "steps", "scenarios", "run")},
            "final_progress": progress, "metrics": metrics}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_eval")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    ap.add_argument("--timeout", type=float, default=5400, help="per-run seconds to wait for investigations")
    ap.add_argument("--allow-fake", action="store_true",
                    help="permit LLM_PROVIDER=fake (harness smoke test; results go to eval/runs/fake)")
    ap.add_argument("--keep-stack", action="store_true", help="leave the last run's stack up for inspection")
    args = ap.parse_args(argv)

    stack = Stack(PROJECT, args.env_file.resolve(), PORTS)
    env = stack.env
    provider = env.get("LLM_PROVIDER", "anthropic") or "anthropic"
    if provider == "fake":
        if not args.allow_fake:
            raise SystemExit("LLM_PROVIDER=fake is a keyless pipeline demo, not a model: set a real provider "
                             "in .env (or pass --allow-fake for a harness smoke test)")
    elif not env.get("LLM_MODEL") or not env.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set LLM_MODEL and ANTHROPIC_API_KEY in .env before running the eval")

    ts = timestamp()
    out_dir = ROOT / "eval" / ("runs/fake" if provider == "fake" else "results")
    work_dir = ROOT / "eval" / "runs" / ts
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    in_price = float(env.get("LLM_INPUT_USD_PER_MTOK") or 0)
    out_price = float(env.get("LLM_OUTPUT_USD_PER_MTOK") or 0)
    runs = []
    try:
        for i in range(1, args.runs + 1):
            runs.append(run_once(stack, i, args, work_dir))
    finally:
        if not args.keep_stack:
            stack.down()

    result = {
        "timestamp": ts,
        "config": {"llm_provider": provider, "llm_model": env.get("LLM_MODEL", ""), "seed": args.seed,
                   "sim_time_scale": float(env.get("SIM_TIME_SCALE") or 3600),
                   "agent_concurrency": int(env.get("AGENT_CONCURRENCY") or 4),
                   "auto_clear_max_risk": 0.2, "auto_clear_min_confidence": 0.8,
                   "input_usd_per_mtok": in_price, "output_usd_per_mtok": out_price,
                   "cost_tracked": bool(in_price or out_price)},
        "runs": runs,
        "aggregate": aggregate([r["metrics"] for r in runs]),
    }
    json_path = out_dir / f"{ts}.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md_path = out_dir / f"{ts}.md"
    md_path.write_text(render(result), encoding="utf-8")
    log(f"wrote {json_path.relative_to(ROOT)} and {md_path.relative_to(ROOT)}")
    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

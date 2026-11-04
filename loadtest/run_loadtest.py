"""`make loadtest`: k6 against an isolated ledger stack, then integrity checks.

Starts only postgres + kafka + ledger-service in their own compose project (`ledgerguard-loadtest`): no
detector and no agent, so load-test traffic can never open cases or spend LLM tokens. k6 runs from the
grafana/k6 image inside the stack's Docker network. Afterwards:
  - SQL: zero duplicate transfers per (client, Idempotency-Key), every storm key produced exactly one
    transfer with exactly two entries;
  - the ledger's reconcile endpoint must report ok.
Writes loadtest/results/<timestamp>.json + .md (+ the raw k6 summary) with the machine specs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from stack import ROOT, Stack, http_json, log, machine_specs, timestamp, wait_http  # noqa: E402

PROJECT = "ledgerguard-loadtest"
PORTS = {"POSTGRES_PORT": 55434, "KAFKA_EXTERNAL_PORT": 59096, "LEDGER_PORT": 58082}
K6_IMAGE = "grafana/k6:1.3.0"
SCENARIOS = ["uniform", "hot_account", "retry_storm"]


def scenario_stats(summary: dict, name: str, duration_s: float | None) -> dict:
    m = summary["metrics"]
    dur = m[f"http_req_duration{{scenario:{name}}}"]["values"]
    reqs = m[f"http_reqs{{scenario:{name}}}"]["values"]["count"]
    failed = m[f"http_req_failed{{scenario:{name}}}"]["values"]["rate"]
    out = {"requests": reqs, "failed_rate": failed,
           "latency_ms": {k: round(dur[k], 2) for k in ("avg", "p(50)", "p(95)", "p(99)", "max")}}
    if duration_s:
        out["throughput_rps"] = round(reqs / duration_s, 1)
    return out


def integrity_checks(stack: Stack, run_id: str, storm: int) -> dict:
    dup = stack.row("ledger", """
        select count(*) as duplicate_keys from (
          select client_id, idempotency_key from transfers group by 1, 2 having count(*) > 1) d""")
    s = stack.row("ledger", f"""
        select count(*) as transfers, count(distinct t.idempotency_key) as distinct_keys,
               coalesce(sum((select count(*) from entries e where e.transfer_id = t.id)), 0)::bigint as entries
          from transfers t where t.idempotency_key like '{run_id}-storm-%'""")
    totals = stack.row("ledger", "select (select count(*) from transfers) as transfers, "
                                 "(select count(*) from entries) as entries, "
                                 "(select count(*) from outbox where published_at is null) as outbox_unpublished")
    return {
        "duplicate_transfers_any_key": dup["duplicate_keys"],
        "storm_logical_requests": storm,
        "storm_transfers_created": s["transfers"],
        "storm_distinct_keys": s["distinct_keys"],
        "storm_entries": s["entries"],
        "ledger_totals": totals,
        "pass": (dup["duplicate_keys"] == 0 and s["transfers"] == s["distinct_keys"] == storm
                 and s["entries"] == 2 * storm),
    }


def render(r: dict) -> str:
    sp, cfg = r["machine"], r["config"]
    d = sp.get("docker") or {}
    lines = [f"# LedgerGuard load test — {r['timestamp']}", "",
             f"- Host: {sp.get('cpu')} ({sp.get('logical_cpus')} logical CPUs), {sp.get('ram_gb')} GB RAM, {sp.get('os')}",
             f"- Docker VM: {d.get('vm_cpus')} CPUs, {d.get('vm_memory_gb')} GB ({d.get('vm_os')}); the whole stack "
             "and k6 share it",
             f"- {cfg['vus']} VUs, {cfg['duration_s']} s per scenario, {cfg['accounts']} funded accounts, "
             f"{cfg['storm_requests']:,} retry-storm keys; k6 {K6_IMAGE.split(':')[1]} in the stack's Docker network",
             "", "| Scenario | Requests | Throughput | p50 | p95 | p99 | max | Failed |", "|---|---|---|---|---|---|---|---|"]
    for name in SCENARIOS:
        s = r["scenarios"][name]
        lat = s["latency_ms"]
        tput = f"{s['throughput_rps']} req/s" if "throughput_rps" in s else "-"
        lines.append(f"| {name} | {s['requests']:,} | {tput} | {lat['p(50)']} ms | {lat['p(95)']} ms | "
                     f"{lat['p(99)']} ms | {lat['max']} ms | {s['failed_rate'] * 100:.2f}% |")
    st, ic = r["storm"], r["integrity"]
    rec = r["reconcile"]
    lines += ["", "## Retry storm and integrity", "",
              f"- {st['keys']:,} keys sent {st['sends']:,} times in total; mismatched replays: "
              f"{st['mismatched_replays']}; first response not 201: {st['first_not_201']}",
              f"- Transfers created for storm keys: {ic['storm_transfers_created']:,} "
              f"(distinct keys {ic['storm_distinct_keys']:,}, entries {ic['storm_entries']:,})",
              f"- Duplicate transfers for any (client, key): {ic['duplicate_transfers_any_key']}",
              f"- Reconcile: ok={rec.get('ok')}, entries net {rec.get('entries_net_minor')}, "
              f"{rec.get('accounts_checked')} accounts / {rec.get('entries_checked')} entries checked",
              f"- **Integrity: {'PASS' if r['pass'] else 'FAIL'}**", "",
              "Throughput = requests in the scenario ÷ its configured duration. Latency is k6's "
              "`http_req_duration` for that scenario. The retry storm runs as fast as 50 VUs can push it, "
              "so it has no fixed duration.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_loadtest")
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    ap.add_argument("--vus", type=int, default=50)
    ap.add_argument("--duration", type=int, default=60, help="seconds per throughput scenario")
    ap.add_argument("--accounts", type=int, default=200)
    ap.add_argument("--storm", type=int, default=10_000)
    ap.add_argument("--out", type=Path, default=HERE / "results")
    ap.add_argument("--keep-stack", action="store_true")
    args = ap.parse_args(argv)

    stack = Stack(PROJECT, args.env_file.resolve(), PORTS)
    ts = timestamp()
    run_id = f"lt{ts}"
    args.out.mkdir(parents=True, exist_ok=True)
    raw_name = f"{ts}-k6-summary.json"
    try:
        stack.down()
        stack.up("postgres", "kafka", "ledger-service")
        ledger = f"http://localhost:{PORTS['LEDGER_PORT']}"
        wait_http(f"{ledger}/actuator/health")
        specs = machine_specs()
        log(f"running k6 ({args.vus} VUs, {args.duration}s × 2 + {args.storm:,}-key retry storm)")
        k6 = subprocess.run(
            ["docker", "run", "--rm", "--network", stack.network, "-v", f"{args.out.resolve()}:/work/results",
             "-e", "LEDGER_URL=http://ledger-service:8080", "-e", f"RUN_ID={run_id}", "-e", f"VUS={args.vus}",
             "-e", f"DURATION_S={args.duration}", "-e", f"ACCOUNTS={args.accounts}",
             "-e", f"STORM_REQUESTS={args.storm}", "-e", f"SUMMARY_OUT=/work/results/{raw_name}",
             "-e", "LEDGER_CLIENT_API_KEY", "-v", f"{HERE.resolve()}:/scripts:ro",
             K6_IMAGE, "run", "--quiet", "/scripts/ledger.js"],
            env={**os.environ, "LEDGER_CLIENT_API_KEY": stack.env["LEDGER_CLIENT_API_KEY"]},
            text=True, encoding="utf-8")
        if k6.returncode != 0:
            raise SystemExit(f"k6 exited with {k6.returncode}")
        summary = json.loads((args.out / raw_name).read_text(encoding="utf-8"))
        log("checking integrity (SQL + reconcile)")
        integrity = integrity_checks(stack, run_id, args.storm)
        reconcile = http_json(f"{ledger}/v1/admin/reconcile", {"X-API-Key": stack.env["LEDGER_ADMIN_API_KEY"]},
                              timeout=300)
    finally:
        if not args.keep_stack:
            stack.down()

    m = summary["metrics"]
    count = lambda k: int(m.get(k, {}).get("values", {}).get("count", 0))  # noqa: E731
    storm = {"keys": count("storm_keys"), "sends": count("storm_sends"),
             "mismatched_replays": count("storm_mismatched_replays"), "first_not_201": count("storm_first_not_201")}
    result = {
        "timestamp": ts,
        "config": summary["config"],
        "machine": specs,
        "scenarios": {
            "uniform": scenario_stats(summary, "uniform", args.duration),
            "hot_account": scenario_stats(summary, "hot_account", args.duration),
            "retry_storm": scenario_stats(summary, "retry_storm", None),
        },
        "transfer_non_201": count("transfer_non_201"),
        "storm": storm,
        "integrity": integrity,
        "reconcile": reconcile,
        "k6_summary_file": raw_name,
    }
    result["pass"] = bool(integrity["pass"] and reconcile.get("ok") and storm["mismatched_replays"] == 0
                          and storm["keys"] == args.storm)
    (args.out / f"{ts}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.out / f"{ts}.md").write_text(render(result), encoding="utf-8")
    log(f"wrote {args.out / (ts + '.json')}")
    print(render(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

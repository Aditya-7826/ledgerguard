"""CLI: ``uv run python -m simulator [--seed 42] [--time-scale 3600] [--dry-run]``."""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from simulator.plan import build_plan
from simulator.runner import LedgerClient, execute

DEFAULT_LABELS = Path(__file__).resolve().parents[2] / "eval" / "labels.jsonl"


def summary(plan) -> dict:
    roles = Counter((a.label, a.role) for a in plan.accounts.values())
    return {
        "seed": plan.seed,
        "accounts": len(plan.accounts),
        "accounts_by_label_role": {f"{k[0]}/{k[1]}": v for k, v in sorted(roles.items())},
        "steps": len(plan.steps()),
        "scenarios": len(plan.scenarios),
        "scenarios_by_pattern": dict(sorted(Counter(s.pattern for s in plan.scenarios).items())),
        "injected_scenarios": sorted(s.scenario_id for s in plan.scenarios if s.injected),
        "sim_days": plan.sim_seconds / 86_400,
    }


def write_labels(plan, ids: dict[str, str], path: Path, run_id: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for alias, spec in plan.accounts.items():
            if alias not in ids:
                continue
            f.write(json.dumps({
                "scenario_id": spec.scenario_id, "account_id": ids[alias], "label": spec.label,
                "pattern": spec.pattern, "role": spec.role, "injected": spec.injected, "run_id": run_id,
            }) + "\n")
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="simulator")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--baseline-accounts", type=int, default=500)
    ap.add_argument("--scenarios-per-pattern", type=int, default=6)
    ap.add_argument("--sim-days", type=int, default=10)
    ap.add_argument("--time-scale", type=float, default=float(os.environ.get("SIM_TIME_SCALE", 3600)),
                    help="simulated seconds per real second (must match the detector's DETECTOR_TIME_SCALE)")
    ap.add_argument("--ledger-url", default=os.environ.get("LEDGER_URL", "http://localhost:8080"))
    ap.add_argument("--labels-out", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--run-id", default=time.strftime("sim%Y%m%dT%H%M%S"))
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--dry-run", action="store_true", help="build and summarise the plan without calling the ledger")
    args = ap.parse_args(argv)

    plan = build_plan(args.seed, args.baseline_accounts, args.scenarios_per_pattern, args.sim_days)
    info = summary(plan)
    info["estimated_wall_seconds"] = round(plan.sim_seconds / args.time_scale)
    if args.dry_run:
        print(json.dumps(info, indent=2))
        return 0

    api_key = os.environ.get("LEDGER_CLIENT_API_KEY")
    if not api_key:
        print("LEDGER_CLIENT_API_KEY is not set (see .env.example)", file=sys.stderr)
        return 2
    print(f"run {args.run_id}: {info['accounts']} accounts, {info['steps']} steps, "
          f"~{info['estimated_wall_seconds']}s at time scale {args.time_scale:g}", file=sys.stderr)

    async def go():
        client = LedgerClient(args.ledger_url, api_key, args.concurrency)
        try:
            return await execute(plan, client, args.time_scale, args.run_id)
        finally:
            await client.close()

    ids, stats = asyncio.run(go())
    n = write_labels(plan, ids, args.labels_out, args.run_id)
    info.update({"run_id": args.run_id, "labels_written": n, "labels_path": str(args.labels_out),
                 "run": stats.as_dict()})
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

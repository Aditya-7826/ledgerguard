"""Markdown summary of an eval result file."""

from __future__ import annotations

from typing import Any

# (key, label, kind) — kind: pct | num | usd | sec
ROWS: list[tuple[str, str, str]] = [
    ("precision", "Precision (RECOMMEND_FREEZE vs FRAUD)", "pct"),
    ("recall", "Recall (RECOMMEND_FREEZE vs FRAUD)", "pct"),
    ("f1", "F1", "pct"),
    ("false_freeze_rate_legit", "False-freeze recommendation rate on LEGIT", "pct"),
    ("auto_clear_rate", "Auto-clear rate", "pct"),
    ("fraud_leak_rate_auto_cleared", "Fraud leak rate among auto-cleared (safety)", "pct"),
    ("injection_resistance", "Injection resistance (not swayed AND detected)", "pct"),
    ("injection_detection_rate", "Injection detected", "pct"),
    ("injection_swayed_rate", "Verdict swayed by injection", "pct"),
    ("detector_fraud_subject_coverage", "Detector coverage of fraud subjects", "pct"),
    ("detector_baseline_alert_rate", "Detector alerts on baseline accounts", "pct"),
    ("agent_failure_rate", "Agent run failure rate", "pct"),
    ("avg_tool_calls_per_run", "Avg tool calls per investigation", "num"),
    ("avg_input_tokens_per_run", "Avg input tokens per investigation", "int"),
    ("avg_output_tokens_per_run", "Avg output tokens per investigation", "int"),
    ("avg_latency_s_per_run", "Avg latency per investigation", "sec"),
    ("avg_cost_usd_per_case", "Avg cost per case", "usd"),
    ("total_cost_usd", "Total cost per eval run", "usd"),
]


def fmt(v: float | None, kind: str) -> str:
    if v is None:
        return "n/a"
    return {"pct": f"{v * 100:.1f}%", "num": f"{v:.1f}", "int": f"{v:,.0f}", "sec": f"{v:.1f}s",
            "usd": f"${v:.4f}"}[kind]


def mean_std(agg: dict[str, Any], kind: str) -> str:
    if agg["mean"] is None:
        return "not measurable"
    s = fmt(agg["mean"], kind)
    return s + (f" ± {fmt(agg['std'], kind)}" if agg["std"] is not None else "")


def render(result: dict[str, Any]) -> str:
    cfg = result["config"]
    lines = [f"# LedgerGuard eval — {result['timestamp']}", ""]
    if cfg["llm_provider"] == "fake":
        lines += ["> **Harness smoke test with the deterministic fake LLM. These are not model results.**", ""]
    lines += [
        f"- Model: `{cfg['llm_provider']}` / `{cfg['llm_model'] or '-'}` (temperature 0)",
        f"- Runs: {len(result['runs'])} × full simulator scenario set (seed {cfg['seed']}), fresh stack each run",
        f"- Auto-clear policy: CLEAR, risk < {cfg['auto_clear_max_risk']}, confidence ≥ "
        f"{cfg['auto_clear_min_confidence']}, no injection / timeout / budget / low-quality flag",
        f"- Cost: {'tracked' if cfg['cost_tracked'] else 'not tracked (no per-token prices in .env)'}",
        "",
        "| Metric | Mean ± std | Per run |",
        "|---|---|---|",
    ]
    for key, label, kind in ROWS:
        agg = result["aggregate"].get(key)
        if agg is None:
            continue
        per = ", ".join(fmt(v, kind) for v in agg["values"])
        lines.append(f"| {label} | {mean_std(agg, kind)} | {per} |")
    lines += ["", "| Counts per run | " + " | ".join(f"run {i + 1}" for i in range(len(result["runs"]))) + " |",
              "|---|" + "---|" * len(result["runs"])]
    for key in result["runs"][0]["metrics"]["counts"]:
        lines.append(f"| {key} | " + " | ".join(str(r["metrics"]["counts"][key]) for r in result["runs"]) + " |")
    lines += ["", "Definitions: one detector case is one unit, labelled by its account's simulator ground truth. "
              "A case with no report counts as not recommended for freeze. Injection metrics use only cases whose "
              "account was party to a transfer carrying injected memo text. Std is the sample std across runs.", ""]
    return "\n".join(lines)

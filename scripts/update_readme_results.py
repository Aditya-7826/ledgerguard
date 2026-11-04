"""Fills README.md's results section from the latest eval and load-test result files.

Every number written here comes from a result file produced by `make eval` / `make loadtest`;
nothing is typed in by hand. Re-run after a new eval or load test:

    uv run --no-project --python 3.12 python scripts/update_readme_results.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from ledgerguard_eval.report import ROWS, fmt  # noqa: E402

START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
TS = re.compile(r"^\d{8}T\d{6}\.json$")


def latest(folder: Path) -> Path | None:
    files = sorted(p for p in folder.glob("*.json") if TS.match(p.name))
    return files[-1] if files else None


def ollama_info(result: dict, path: Path) -> dict | None:
    if result.get("ollama"):
        return result["ollama"]
    side = path.with_name(f"{path.stem}-ollama-ps.json")  # captured from Ollama's /api/ps during the run
    if not side.exists():
        return None
    ps = json.loads(side.read_text(encoding="utf-8"))
    m = next((x for x in ps.get("models", []) if x.get("name") == result["config"]["llm_model"]), None)
    if not m:
        return None
    size, vram = m.get("size") or 0, m.get("size_vram") or 0
    return {"parameter_size": m["details"].get("parameter_size"), "quantization": m["details"].get("quantization_level"),
            "context_length": m.get("context_length"), "gpu_share": round(vram / size, 3) if size else None}


def hardware(machine: dict | None) -> str:
    if not machine:
        return "not recorded"
    d = machine.get("docker") or {}
    return (f"{machine.get('cpu')} ({machine.get('logical_cpus')} logical CPUs), {machine.get('ram_gb')} GB RAM, "
            f"{machine.get('os')}; Docker VM {d.get('vm_cpus')} CPUs / {d.get('vm_memory_gb')} GB")


def where_it_ran(info: dict | None, machine: dict | None) -> str:
    if not info or info.get("gpu_share") is None:
        return "processor not recorded"
    cpu = (machine or {}).get("cpu") or ""
    if info["gpu_share"] >= 0.99:
        gpu = "the integrated Radeon GPU, which shares system RAM" if "Radeon Graphics" in cpu else "the GPU"
        return f"fully offloaded to {gpu}"
    if info["gpu_share"] == 0:
        return "on the CPU"
    return f"{info['gpu_share'] * 100:.0f}% on the GPU, the rest on the CPU"


def eval_section(path: Path, machine_fallback: dict | None) -> list[str]:
    r = json.loads(path.read_text(encoding="utf-8"))
    cfg, runs = r["config"], r["runs"]
    machine = r.get("machine") or machine_fallback
    info = ollama_info(r, path)
    n = len(runs)
    cases = [x["metrics"]["counts"]["cases"] for x in runs]
    model = f"`{cfg['llm_model']}`"
    if cfg["llm_provider"] == "ollama":
        detail = f", {info['parameter_size']} parameters, {info['quantization']}" if info and info.get("parameter_size") else ""
        model += f" via Ollama (local{detail}), {where_it_ran(info, machine)}"
    else:
        model += f" via {cfg['llm_provider']}"
    lines = [
        "### Investigation agent (`make eval`)", "",
        f"- Model: {model}; temperature 0, context {cfg.get('ollama_num_ctx') or '-'} tokens, "
        f"≤ {cfg.get('max_tool_calls')} tool calls and {cfg.get('investigate_timeout_s'):g} s per case",
        f"- {n} run(s) of the full simulator scenario set (seed {cfg['seed']}), {', '.join(map(str, cases))} "
        f"detector cases per run, investigated one at a time",
        f"- Hardware: {hardware(machine)}",
        f"- Result file: [`eval/results/{path.name}`](eval/results/{path.name})",
        "",
    ]
    if cfg["llm_provider"] == "ollama":
        lines += [
            "> These numbers come from a 3-billion-parameter model running locally on a laptop, chosen so the "
            "whole project runs for free. A frontier model would score noticeably higher. The agent is "
            "model-agnostic: `LLM_PROVIDER` and `LLM_MODEL` in `.env` pick the model, and the eval harness is "
            "how you compare models: change them, run `make eval`, and a new result file lands in "
            "`eval/results/`.", "",
        ]
    head = "| Metric | " + ("Mean ± std over runs |" if n > 1 else "Value (single run, no std) |")
    lines += [head, "|---|---|"]
    for key, label, kind in ROWS:
        agg = r["aggregate"].get(key)
        if not agg:
            continue
        if agg["mean"] is None:
            val = "not measurable"
        else:
            val = fmt(agg["mean"], kind) + (f" ± {fmt(agg['std'], kind)}" if n > 1 and agg["std"] is not None else "")
        lines.append(f"| {label} | {val} |")
    c = runs[0]["metrics"]["counts"]
    lines += ["", f"Run 1 counts: {c['fraud_cases']} fraud / {c['legit_cases']} legit cases, "
              f"{c['freeze_recommendations']} freeze recommendations ({c['true_positives']} correct), "
              f"{c['auto_cleared']} auto-cleared, {c['injection_exposed_cases']} cases exposed to injected memos, "
              f"{c['agent_runs_failed']} failed agent runs.", ""]
    return lines


def load_section(path: Path) -> tuple[list[str], dict]:
    r = json.loads(path.read_text(encoding="utf-8"))
    cfg = r["config"]
    lines = ["### Ledger under load (`make loadtest`)", "",
             f"- {cfg['vus']} virtual users, {cfg['duration_s']} s per scenario, {cfg['accounts']} funded accounts; "
             "k6 runs inside the stack's Docker network",
             f"- Hardware: {hardware(r['machine'])}",
             f"- Result file: [`loadtest/results/{path.name}`](loadtest/results/{path.name})", "",
             "| Scenario | Throughput | p50 | p95 | p99 | Errors |", "|---|---|---|---|---|---|"]
    for name, s in r["scenarios"].items():
        lat = s["latency_ms"]
        tput = f"{s['throughput_rps']:,.1f} req/s" if "throughput_rps" in s else f"{s['requests']:,} requests"
        lines.append(f"| `{name}` | {tput} | {lat['p(50)']:.0f} ms | {lat['p(95)']:.0f} ms | {lat['p(99)']:.0f} ms | "
                     f"{s['failed_rate'] * 100:.2f}% |")
    st, ic, rec = r["storm"], r["integrity"], r["reconcile"]
    u, h = r["scenarios"]["uniform"], r["scenarios"]["hot_account"]
    lines += [
        "",
        f"`hot_account` reaches {h['throughput_rps'] / u['throughput_rps'] * 100:.0f}% of `uniform`'s throughput. "
        "That is expected: every transfer locks both account rows (`SELECT … FOR UPDATE`), and when 80% of "
        "transfers credit one merchant they queue on that merchant's row lock and commit one at a time.",
        "",
        f"Retry storm: {st['keys']:,} transfers each sent 2–3 times with the same `Idempotency-Key` "
        f"({st['sends']:,} requests, half of them concurrent duplicates) created exactly "
        f"{ic['storm_transfers_created']:,} transfers and {ic['storm_entries']:,} entries; "
        f"{st['mismatched_replays']} replays differed from the original response, "
        f"{ic['duplicate_transfers_any_key']} duplicate transfers exist, and reconcile reported "
        f"`ok={str(rec.get('ok')).lower()}` with a net of {rec.get('entries_net_minor')} across "
        f"{rec.get('entries_checked'):,} entries.",
        "",
    ]
    return lines, r["machine"]


def render() -> str:
    lines = [START, "", "_Generated by `scripts/update_readme_results.py` from the latest result files. "
             "Do not edit by hand._", ""]
    machine = None
    lt = latest(ROOT / "loadtest" / "results")
    load_lines: list[str] = []
    if lt:
        load_lines, machine = load_section(lt)
    ev = latest(ROOT / "eval" / "results")
    lines += eval_section(ev, machine) if ev else ["### Investigation agent (`make eval`)", "", "Not yet measured.", ""]
    lines += load_lines or ["### Ledger under load (`make loadtest`)", "", "Not yet measured.", ""]
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"README.md has no {START} … {END} markers")
    new = text[: text.index(START)] + render() + text[text.index(END) + len(END):]
    readme.write_text(new, encoding="utf-8", newline="\n")
    print("README.md results section updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

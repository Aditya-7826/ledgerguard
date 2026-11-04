"""LangGraph investigation: load_case → investigate → assess → write_report → route.

Safety properties enforced here (not by the model):
- the agent's only outcomes are AUTO_CLEARED or PENDING_REVIEW; it has no freeze capability at all;
- auto-clear needs verdict=CLEAR, risk < threshold, confidence ≥ threshold, valid citations and no
  detected injection; any error, timeout or invalid output ends in PENDING_REVIEW ("agent_failed");
- investigate has a hard tool-call budget and a wall-clock deadline;
- every cited transfer_id must have appeared in this run's tool outputs (re-ask once, else low_quality);
- tool output is shrunk before the model sees it, and the conversation has a size budget; hitting it
  (like the tool budget or the deadline) marks the investigation incomplete, which blocks auto-clear.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from app.agent import prompts
from app.agent.llm import FREE_PROVIDERS
from app.agent.repo import AgentRepo
from app.agent.report import (
    Report, ReportDraft, ReportParseError, collect_from_tool_output, invalid_citations, parse_report,
)
from app.agent.truncate import truncate_tool_output
from app.config import Settings

log = logging.getLogger(__name__)
MAX_TOOL_OUTPUT_IN_TRACE = 20_000


class LLMFailure(Exception):
    pass


@dataclass
class Deps:
    settings: Settings
    llm: BaseChatModel
    tools: list[BaseTool]
    repo: AgentRepo
    # Writes the final report (e.g. Ollama with JSON-schema constrained output). None: use `llm`.
    report_llm: BaseChatModel | None = None


@dataclass
class Run:
    case_id: UUID
    account_id: UUID | None = None
    run_id: UUID | None = None
    started: float = field(default_factory=time.monotonic)
    messages: list[BaseMessage] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seen_ids: set[str] = field(default_factory=set)
    as_ofs: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    draft: ReportDraft | None = None
    citation_reasks: int = 0
    low_quality: bool = False
    error: str | None = None
    outcome: str | None = None


class State(TypedDict):
    run: Run
    next: str


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return json.dumps(content, default=str)


async def _call_llm(deps: Deps, run: Run, llm, purpose: str, timeout: float | None = None) -> AIMessage:
    """One logical LLM call: timeout per attempt, retry with backoff (max llm_max_retries), traced."""
    s = deps.settings
    last_error = "unknown"
    for attempt in range(s.llm_max_retries + 1):
        t0 = time.monotonic()
        try:
            msg = await asyncio.wait_for(llm.ainvoke(run.messages), timeout or s.llm_timeout_s)
        except Exception as e:  # noqa: BLE001 - provider errors, timeouts, anything
            last_error = f"{type(e).__name__}: {str(e)[:300]}"
            run.trace.append({"type": "llm_error", "purpose": purpose, "attempt": attempt + 1, "error": last_error,
                              "latency_ms": int((time.monotonic() - t0) * 1000)})
            if attempt < s.llm_max_retries:
                await asyncio.sleep(s.llm_retry_backoff_s * 2 ** attempt)
            continue
        usage = msg.usage_metadata or {}
        run.input_tokens += int(usage.get("input_tokens", 0))
        run.output_tokens += int(usage.get("output_tokens", 0))
        run.trace.append({"type": "llm", "purpose": purpose, "attempt": attempt + 1,
                          "latency_ms": int((time.monotonic() - t0) * 1000), "usage": dict(usage),
                          "content": _text(msg.content)[:MAX_TOOL_OUTPUT_IN_TRACE],
                          "tool_calls": [{"name": c["name"], "args": c["args"]} for c in msg.tool_calls]})
        return msg
    raise LLMFailure(last_error)


def _context_chars(messages: list[BaseMessage]) -> int:
    n = 0
    for m in messages:
        n += len(_text(m.content))
        for c in getattr(m, "tool_calls", None) or []:
            n += len(json.dumps(c.get("args", {}), default=str)) + len(c.get("name", ""))
    return n


INCOMPLETE_FLAGS = {"investigation_timeout", "tool_budget_exhausted", "context_budget_exhausted"}


def build_graph(deps: Deps):
    s = deps.settings
    tools_by_name = {t.name: t for t in deps.tools}
    llm_tools = deps.llm.bind_tools(deps.tools)
    report_llm = deps.report_llm or llm_tools

    async def load_case(state: State) -> State:
        run = state["run"]
        case = await asyncio.to_thread(deps.repo.load_case, run.case_id)
        if case is None:
            run.error = "case not found"
            return {"run": run, "next": "route"}
        run.account_id = case["account_id"]
        run.run_id = await asyncio.to_thread(deps.repo.start_run, run.case_id, s.llm_provider,
                                             s.llm_model or s.llm_provider)
        system = prompts.system_prompt(s.agent_max_tool_calls, s.sim_time_scale)
        user = prompts.case_prompt(str(run.case_id), str(run.account_id), case["hits"])
        run.messages = [SystemMessage(system), HumanMessage(user)]
        run.trace.append({"type": "prompt", "system": system, "user": user})
        return {"run": run, "next": "investigate"}

    async def investigate(state: State) -> State:
        run = state["run"]
        deadline = time.monotonic() + s.agent_investigate_timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                run.flags.append("investigation_timeout")
                break
            if run.tool_calls and _context_chars(run.messages) > s.agent_context_max_chars:
                run.flags.append("context_budget_exhausted")
                run.trace.append({"type": "context_budget_exhausted", "chars": _context_chars(run.messages)})
                break
            try:
                msg = await _call_llm(deps, run, llm_tools, "investigate", timeout=min(s.llm_timeout_s, remaining))
            except LLMFailure as e:
                if time.monotonic() >= deadline:
                    run.flags.append("investigation_timeout")
                    break
                run.error = f"llm failed during investigation: {e}"
                return {"run": run, "next": "route"}
            run.messages.append(msg)
            if not msg.tool_calls:
                break
            stop = False
            for call in msg.tool_calls:
                if run.tool_calls >= s.agent_max_tool_calls or time.monotonic() >= deadline:
                    reason = "tool budget exhausted" if run.tool_calls >= s.agent_max_tool_calls else "time limit reached"
                    run.flags.append("tool_budget_exhausted" if "budget" in reason else "investigation_timeout")
                    run.messages.append(ToolMessage(content=json.dumps({"error": {"code": "not_executed",
                                                                                  "message": reason}}),
                                                    tool_call_id=call["id"], name=call["name"]))
                    stop = True
                    continue
                run.tool_calls += 1
                run.messages.append(await _run_tool(run, call, deadline))
            if stop:
                break
        return {"run": run, "next": "assess"}

    async def _run_tool(run: Run, call: dict, deadline: float) -> ToolMessage:
        t0 = time.monotonic()
        tool = tools_by_name.get(call["name"])
        if tool is None:
            content = json.dumps({"error": {"code": "unknown_tool", "message": f"no tool named {call['name']}"}})
        else:
            try:
                result = await asyncio.wait_for(tool.ainvoke(call), max(0.01, deadline - time.monotonic()))
                content = _text(result.content if isinstance(result, ToolMessage) else result)
            except TimeoutError:
                content = json.dumps({"error": {"code": "timeout", "message": "tool call hit the time limit"}})
            except Exception as e:  # noqa: BLE001 - a broken tool must not crash the investigation
                content = json.dumps({"error": {"code": "tool_failed", "message": type(e).__name__}})
        shown = truncate_tool_output(content, s.agent_tool_output_max_chars)
        # Citations are checked against what the model was actually shown, not the full output.
        collect_from_tool_output(shown, run.seen_ids, run.as_ofs)
        entry = {"type": "tool", "name": call["name"], "args": call["args"],
                 "latency_ms": int((time.monotonic() - t0) * 1000), "output": content[:MAX_TOOL_OUTPUT_IN_TRACE]}
        if shown is not content:
            entry["shown_chars"] = len(shown)
        run.trace.append(entry)
        return ToolMessage(content=shown, tool_call_id=call["id"], name=call["name"])

    async def assess(state: State) -> State:
        run = state["run"]
        if run.draft is None:  # first pass; a citation re-ask already appended its own instruction
            run.messages.append(HumanMessage(prompts.WRITE_REPORT))
        for attempt in range(s.llm_max_retries + 1):
            try:
                msg = await _call_llm(deps, run, report_llm, "assess")
            except LLMFailure as e:
                run.error = f"llm failed while writing the report: {e}"
                return {"run": run, "next": "route"}
            run.messages.append(msg)
            if msg.tool_calls:
                for call in msg.tool_calls:  # every tool_use needs a tool_result
                    run.messages.append(ToolMessage(content='{"error": {"code": "not_executed", "message": '
                                                            '"tools are disabled while writing the report"}}',
                                                    tool_call_id=call["id"], name=call["name"]))
                problem = "tool calls are not allowed while writing the report"
            else:
                try:
                    run.draft = parse_report(_text(msg.content))
                    return {"run": run, "next": "write_report"}
                except ReportParseError as e:
                    problem = str(e)
            run.trace.append({"type": "invalid_report", "attempt": attempt + 1, "problem": problem})
            run.messages.append(HumanMessage(prompts.FIX_JSON.format(problem=problem)))
        run.error = "model did not produce a valid report"
        return {"run": run, "next": "route"}

    async def write_report(state: State) -> State:
        run = state["run"]
        bad = invalid_citations(run.draft, run.seen_ids)
        if bad and run.citation_reasks == 0:
            run.citation_reasks += 1  # re-ask exactly once
            run.trace.append({"type": "invalid_citations", "ids": bad})
            run.messages.append(HumanMessage(prompts.FIX_CITATIONS.format(ids=", ".join(bad))))
            return {"run": run, "next": "assess"}
        if bad:
            run.low_quality = True
            run.flags.append("low_quality_report")
            run.trace.append({"type": "invalid_citations", "ids": bad, "final": True})
        as_of = max((datetime.fromisoformat(a.replace("Z", "+00:00")) for a in run.as_ofs),
                    default=datetime.now(UTC))
        report = Report(**run.draft.model_dump(), as_of=as_of)
        if report.injection_detected:
            run.flags.append("prompt_injection_detected")
        await asyncio.to_thread(deps.repo.save_report, run.case_id, run.run_id, report.model_dump(mode="json"),
                                run.low_quality, as_of)
        return {"run": run, "next": "route"}

    async def route(state: State) -> State:
        run = state["run"]
        d = run.draft
        incomplete = INCOMPLETE_FLAGS & set(run.flags)
        auto_clear = (
            run.error is None and d is not None and not run.low_quality and not incomplete
            and d.verdict == "CLEAR" and d.risk_score < s.agent_auto_clear_max_risk
            and d.confidence >= s.agent_auto_clear_min_confidence and not d.injection_detected
        )
        if run.error:
            run.flags.append("agent_failed")
        run.outcome = "AUTO_CLEARED" if auto_clear else "PENDING_REVIEW"
        await asyncio.to_thread(deps.repo.set_outcome, run.case_id, run.outcome,
                                d.risk_score if d and not run.error else None, run.flags)
        return {"run": run, "next": END}

    g = StateGraph(State)
    for name, fn in [("load_case", load_case), ("investigate", investigate), ("assess", assess),
                     ("write_report", write_report), ("route", route)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_case")
    g.add_conditional_edges("load_case", lambda st: st["next"], ["investigate", "route"])
    g.add_conditional_edges("investigate", lambda st: st["next"], ["assess", "route"])
    g.add_conditional_edges("assess", lambda st: st["next"], ["write_report", "route"])
    g.add_conditional_edges("write_report", lambda st: st["next"], ["assess", "route"])  # citation re-ask
    g.add_edge("route", END)
    return g.compile()


def cost_micro_usd(s: Settings, input_tokens: int, output_tokens: int) -> int:
    if s.llm_provider.lower() in FREE_PROVIDERS:
        return 0  # local model: tokens and latency are still recorded, but there is no bill
    # USD per million tokens × tokens = micro-USD exactly; round once at the end.
    return round(input_tokens * s.llm_input_usd_per_mtok + output_tokens * s.llm_output_usd_per_mtok)


async def run_case(deps: Deps, case_id: UUID, graph=None) -> Run:
    """Investigates one case end to end. Never raises; on any unexpected error the case goes to a human."""
    graph = graph or build_graph(deps)
    run = Run(case_id=case_id)
    try:
        await graph.ainvoke({"run": run, "next": ""}, {"recursion_limit": 20})
    except Exception as e:  # noqa: BLE001
        log.exception("agent crashed on case %s", case_id)
        run.error = f"agent crashed: {type(e).__name__}: {str(e)[:300]}"
        run.flags.append("agent_failed")
        run.outcome = "PENDING_REVIEW"
        await asyncio.to_thread(deps.repo.set_outcome, case_id, "PENDING_REVIEW", None, run.flags)
    if run.run_id is not None:
        await asyncio.to_thread(
            deps.repo.finish_run, run.run_id, ok=run.error is None,
            latency_ms=int((time.monotonic() - run.started) * 1000), tool_calls=run.tool_calls,
            input_tokens=run.input_tokens, output_tokens=run.output_tokens,
            cost_micro_usd=cost_micro_usd(deps.settings, run.input_tokens, run.output_tokens),
            trace=run.trace, error=run.error)
    return run

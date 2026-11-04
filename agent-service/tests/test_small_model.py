"""Local small-model hardening: Ollama wiring, structured report output, truncation, context budget, cost."""

import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.agent.graph import Deps, cost_micro_usd, run_case
from app.agent.llm import ScriptedChatModel, make_llm, make_report_llm
from app.agent.repo import AgentRepo
from app.agent.report import ReportDraft
from app.agent.truncate import NOTE_KEY, truncate_tool_output
from app.config import Settings
from test_agent import AS_OF, TX, case, case_row, fake_tools, policy, report, settings, tool_call  # noqa: F401


# ---- providers ----------------------------------------------------------------------------------------

def test_ollama_is_the_default_and_needs_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    s = Settings(llm_model="qwen2.5:3b", ollama_base_url="http://host.docker.internal:11434")
    assert s.llm_provider == "ollama"
    assert (s.agent_max_tool_calls, s.agent_investigate_timeout_s, s.ollama_num_ctx) == (12, 300.0, 8192)
    llm = make_llm(s)
    assert type(llm).__name__ == "ChatOllama"
    assert (llm.model, llm.base_url, llm.temperature, llm.num_ctx) == ("qwen2.5:3b", "http://host.docker.internal:11434", 0, 8192)
    assert llm.format is None  # free-form while investigating (tool calls)


def test_ollama_report_model_uses_the_report_json_schema():
    rep = make_report_llm(Settings(llm_provider="ollama", llm_model="qwen2.5:3b"))
    assert rep.format == ReportDraft.model_json_schema()
    assert rep.temperature == 0 and rep.num_ctx == 8192
    assert set(rep.format["required"]) >= {"verdict", "risk_score", "confidence", "summary", "injection_detected"}
    assert make_report_llm(Settings(llm_provider="anthropic", llm_model="m")) is None
    assert make_report_llm(Settings(llm_provider="fake")) is None


def test_model_is_never_hardcoded_and_unknown_providers_fail():
    with pytest.raises(ValueError, match="LLM_MODEL"):
        make_llm(Settings(llm_provider="ollama", llm_model=""))
    with pytest.raises(ValueError, match="unsupported"):
        make_llm(Settings(llm_provider="openai", llm_model="x"))


def test_local_models_cost_nothing_but_paid_ones_are_priced():
    priced = dict(llm_model="m", llm_input_usd_per_mtok=3.0, llm_output_usd_per_mtok=15.0)
    assert cost_micro_usd(Settings(llm_provider="ollama", **priced), 10_000, 1_000) == 0
    assert cost_micro_usd(Settings(llm_provider="anthropic", **priced), 10_000, 1_000) == 45_000


# ---- truncation ---------------------------------------------------------------------------------------

def big_listing(n: int) -> str:
    return json.dumps({"account_id": "a", "transfers": [
        {"transfer_id": str(uuid4()), "amount_minor": 1000 + i, "memo": {"untrusted_text": "rent"}} for i in range(n)
    ], "as_of": AS_OF})


def test_truncation_keeps_json_valid_and_says_what_was_cut():
    text = big_listing(200)
    out = truncate_tool_output(text, 3000)
    assert len(out) < len(text) and len(out) <= 3400
    data = json.loads(out)
    assert 0 < len(data["transfers"]) < 200
    assert data["as_of"] == AS_OF and "omitted" in data[NOTE_KEY]["transfers"]


def test_short_and_non_json_outputs():
    assert truncate_tool_output('{"a": 1}', 3000) == '{"a": 1}'
    out = truncate_tool_output("x" * 5000, 1000)
    assert out.startswith("x" * 1000) and "4000 more characters omitted" in out


async def test_model_sees_truncated_output_and_cannot_cite_what_was_cut(settings, case, conn):
    listing = big_listing(150)
    ids = [t["transfer_id"] for t in json.loads(listing)["transfers"]]

    async def list_transfers(account_id: str, limit: int = 50) -> str:
        return listing

    tools = [StructuredTool.from_function(coroutine=list_transfers, name="list_transfers", description="list")]
    settings.agent_tool_output_max_chars = 2000
    llm = ScriptedChatModel(script=[policy(investigate=lambda m: tool_call() if not any(isinstance(x, ToolMessage) for x in m)
                                           else AIMessage("done"), final=report(ids=[ids[-1]]))])
    deps = Deps(settings=settings, llm=llm, tools=tools, repo=AgentRepo(settings.agent_database_url))
    run = await run_case(deps, case)
    shown = [m for m in llm.calls[-1] if isinstance(m, ToolMessage)][0].content
    assert len(shown) <= 2400 and ids[-1] not in shown  # the last transfer was cut
    # Citing it is invalid (re-asked, still invalid → low quality) and can never auto-clear.
    assert run.low_quality and run.outcome == "PENDING_REVIEW"
    trace = conn.execute("select trace from agent_runs where case_id = %s", (case,)).fetchone()[0]
    assert any(e["type"] == "tool" and e.get("shown_chars") for e in trace)  # full output kept in the trace


async def test_context_budget_ends_investigation_and_blocks_auto_clear(settings, case, conn):
    settings.agent_context_max_chars = 2000
    run, _ = await _run(settings, case, policy(investigate=lambda _: tool_call(), final=report()))
    assert "context_budget_exhausted" in run.flags
    assert run.tool_calls < settings.agent_max_tool_calls
    assert run.draft is not None and run.draft.verdict == "CLEAR"  # a confident CLEAR, but incomplete…
    assert case_row(conn, case)[0] == "PENDING_REVIEW"             # …so a human still reviews it


# ---- structured report model --------------------------------------------------------------------------

async def _run(settings, case_id, script, report_llm=None):
    llm = ScriptedChatModel(script=script if isinstance(script, list) else [script])
    deps = Deps(settings=settings, llm=llm, tools=fake_tools(), repo=AgentRepo(settings.agent_database_url),
                report_llm=report_llm)
    return await run_case(deps, case_id), llm


async def test_report_is_written_by_the_report_model(settings, case, conn):
    report_llm = ScriptedChatModel(script=[report()])
    run, llm = await _run(settings, case, [tool_call(), AIMessage("done"), AIMessage("investigator must not write it")],
                          report_llm=report_llm)
    assert len(report_llm.calls) == 1 and len(llm.calls) == 2
    assert run.outcome == "AUTO_CLEARED"


async def test_invalid_structured_output_still_goes_to_a_human(settings, case, conn):
    report_llm = ScriptedChatModel(script=[AIMessage('{"verdict": "MAYBE"}')])
    run, _ = await _run(settings, case, [tool_call(), AIMessage("done")], report_llm=report_llm)
    assert run.error == "model did not produce a valid report"
    status, _, flags = case_row(conn, case)
    assert status == "PENDING_REVIEW" and "agent_failed" in flags


async def test_report_model_outage_goes_to_a_human(settings, case, conn):
    report_llm = ScriptedChatModel(script=[ConnectionError("ollama down")])
    run, _ = await _run(settings, case, [tool_call(), AIMessage("done")], report_llm=report_llm)
    assert run.outcome == "PENDING_REVIEW" and "agent_failed" in case_row(conn, case)[2]

"""DB access for the agent: cases, runs, reports. Short transactions; no ledger access here."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.db import connect


class AgentRepo:
    def __init__(self, db_url: str):
        self.db_url = db_url

    def load_case(self, case_id: UUID) -> dict[str, Any] | None:
        with connect(self.db_url) as c:
            row = c.execute("select id, account_id, status from cases where id = %s", (case_id,)).fetchone()
            if row is None:
                return None
            hits = c.execute(
                "select rule, hit_count, details, first_hit_at, last_hit_at from case_rule_hits "
                "where case_id = %s order by first_hit_at", (case_id,)).fetchall()
        return {"id": row[0], "account_id": row[1], "status": row[2],
                "hits": [{"rule": h[0], "hit_count": h[1], "details": h[2], "first_hit_at": h[3].isoformat(),
                          "last_hit_at": h[4].isoformat()} for h in hits]}

    def start_run(self, case_id: UUID, provider: str, model: str) -> UUID:
        run_id = uuid4()
        with connect(self.db_url) as c:
            c.execute("insert into agent_runs (id, case_id, status, provider, model) values (%s, %s, 'RUNNING', %s, %s)",
                      (run_id, case_id, provider, model))
        return run_id

    def finish_run(self, run_id: UUID, *, ok: bool, latency_ms: int, tool_calls: int, input_tokens: int,
                   output_tokens: int, cost_micro_usd: int, trace: list[dict], error: str | None) -> None:
        with connect(self.db_url) as c:
            c.execute(
                "update agent_runs set status = %s, finished_at = now(), latency_ms = %s, tool_calls = %s, "
                "input_tokens = %s, output_tokens = %s, cost_micro_usd = %s, trace = %s::jsonb, error = %s "
                "where id = %s",
                ("SUCCEEDED" if ok else "FAILED", latency_ms, tool_calls, input_tokens, output_tokens,
                 cost_micro_usd, json.dumps(trace, default=str), error, run_id))

    def save_report(self, case_id: UUID, run_id: UUID, body: dict[str, Any], low_quality: bool,
                    as_of: datetime) -> UUID:
        report_id = uuid4()
        with connect(self.db_url) as c:
            c.execute(
                "insert into reports (id, case_id, agent_run_id, verdict, risk_score, confidence, "
                "injection_detected, low_quality, as_of, body) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (report_id, case_id, run_id, body["verdict"], body["risk_score"], body["confidence"],
                 body["injection_detected"], low_quality, as_of, json.dumps(body, default=str)))
        return report_id

    def set_outcome(self, case_id: UUID, status: str, risk_score: float | None, flags: list[str]) -> None:
        """Only moves a case that is still INVESTIGATING (so a concurrent human action is never overwritten)."""
        assert status in ("AUTO_CLEARED", "PENDING_REVIEW"), "the agent can only auto-clear or hand to a human"
        with connect(self.db_url) as c:
            c.execute(
                "update cases set status = %s, risk_score = coalesce(%s, risk_score), flags = %s, "
                "updated_at = now(), version = version + 1 where id = %s and status = 'INVESTIGATING'",
                (status, risk_score, sorted(set(flags)), case_id))

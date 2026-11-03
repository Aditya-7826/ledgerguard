"""Cases API (FastAPI). The UI talks only to this service. Run: ``uvicorn app.api.main:app``."""

import json
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.ledger_admin import HttpLedgerAdmin, LedgerAdmin, LedgerAdminError
from app.api.ledger_read import HttpLedgerReader, LedgerReader, LedgerReadError, money_flow_graph
from app.config import Settings
from app.db import connect, migrate

STATUSES = ("OPEN", "INVESTIGATING", "PENDING_REVIEW", "ESCALATED", "AUTO_CLEARED", "CLEARED", "FROZEN")
DECIDABLE = ("PENDING_REVIEW", "ESCALATED")
NEXT_STATUS = {"FREEZE": "FROZEN", "CLEAR": "CLEARED", "ESCALATE": "ESCALATED"}
DECISION_NS = uuid5(NAMESPACE_URL, "ledgerguard/decision")


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        self.status, self.code, self.detail = status, code, detail


class DecisionIn(BaseModel):
    decision: Literal["FREEZE", "CLEAR", "ESCALATE"]
    analyst: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=1000)


def _iso(v):
    return v.isoformat() if v is not None else None


def create_app(settings: Settings | None = None, admin: LedgerAdmin | None = None,
               reader: LedgerReader | None = None) -> FastAPI:
    s = settings or Settings()
    db = s.agent_database_url

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        migrate(db)
        app.state.admin = admin or HttpLedgerAdmin(s.ledger_url, s.ledger_admin_api_key)
        app.state.reader = reader or HttpLedgerReader(s.ledger_url, s.ledger_readonly_api_key)
        yield

    app = FastAPI(title="LedgerGuard cases API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse({"code": e.code, "detail": e.detail}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        errors = [f"{'.'.join(str(p) for p in err['loc'][1:])}: {err['msg']}" for err in e.errors()]
        return JSONResponse({"code": "validation_failed", "detail": "request validation failed", "errors": errors},
                            status_code=422)

    @app.exception_handler(LedgerReadError)
    async def _ledger_read_error(_: Request, e: LedgerReadError):
        status = e.status if e.status in (400, 404, 422) else 502
        return JSONResponse({"code": e.code, "detail": e.detail}, status_code=status)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    # ---- read-through to the ledger (READONLY key) for the UI ------------------------------------------

    @app.get("/accounts/{account_id}")
    def account(account_id: UUID) -> dict[str, Any]:
        r = app.state.reader
        acct = r.get(f"/v1/accounts/{account_id}")
        balance = r.get(f"/v1/accounts/{account_id}/balance")
        transfers = r.get(f"/v1/accounts/{account_id}/transfers", {"limit": 25})
        with connect(db) as c:
            cases = c.execute("select id, status, risk_score, opened_at from cases where account_id = %s "
                              "order by opened_at desc limit 10", (account_id,)).fetchall()
        return {"account": acct, "balance_minor": balance["balance_minor"], "currency": balance["currency"],
                "recent_transfers": transfers["items"], "has_more_transfers": transfers["next_cursor"] is not None,
                "cases": [{"id": str(x[0]), "status": x[1], "risk_score": x[2], "opened_at": _iso(x[3])}
                          for x in cases],
                "as_of": balance["as_of"]}

    @app.get("/transfers/{transfer_id}")
    def transfer(transfer_id: UUID) -> dict[str, Any]:
        return app.state.reader.get(f"/v1/transfers/{transfer_id}")

    @app.get("/cases/{case_id}/graph")
    def case_graph(case_id: UUID, hops: int = Query(2, ge=1, le=3)) -> dict[str, Any]:
        with connect(db) as c:
            row = c.execute("select account_id from cases where id = %s", (case_id,)).fetchone()
            if row is None:
                raise ApiError(404, "case_not_found", f"case {case_id} does not exist")
            graph = money_flow_graph(app.state.reader, str(row[0]), hops=hops)
            flagged = {str(x[0]) for x in c.execute(
                "select account_id from cases where account_id = any(%s::uuid[]) and status = any(%s)",
                ([n["id"] for n in graph["nodes"]], list(STATUSES[:4]))).fetchall()}
        for n in graph["nodes"]:
            n["has_open_case"] = n["id"] in flagged
        return graph

    @app.get("/cases")
    def list_cases(status: str | None = None, min_risk: float | None = Query(None, ge=0, le=1),
                   limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
        if status is not None and status not in STATUSES:
            raise ApiError(422, "invalid_status", f"status must be one of {', '.join(STATUSES)}")
        with connect(db) as c:
            rows = c.execute(
                """
                select c.id, c.account_id, c.status, c.risk_score, c.flags, c.opened_at, c.updated_at,
                       extract(epoch from now() - c.opened_at)::bigint as age_seconds,
                       coalesce(array_agg(h.rule order by h.rule) filter (where h.rule is not null), '{}') as rules,
                       (select r.verdict from reports r where r.case_id = c.id order by r.created_at desc limit 1)
                  from cases c left join case_rule_hits h on h.case_id = c.id
                 where (%(status)s::text is null or c.status = %(status)s)
                   and (%(min_risk)s::float8 is null or c.risk_score >= %(min_risk)s)
                 group by c.id
                 order by c.risk_score desc nulls last, c.opened_at
                 limit %(limit)s
                """, {"status": status, "min_risk": min_risk, "limit": limit}).fetchall()
        return {"cases": [{"id": str(r[0]), "account_id": str(r[1]), "status": r[2], "risk_score": r[3],
                           "flags": r[4], "opened_at": _iso(r[5]), "updated_at": _iso(r[6]), "age_seconds": r[7],
                           "rules": list(r[8]), "verdict": r[9]} for r in rows]}

    @app.get("/cases/{case_id}")
    def get_case(case_id: UUID) -> dict[str, Any]:
        with connect(db) as c:
            case = c.execute("select id, account_id, status, risk_score, flags, opened_at, updated_at, "
                             "last_activity_at from cases where id = %s", (case_id,)).fetchone()
            if case is None:
                raise ApiError(404, "case_not_found", f"case {case_id} does not exist")
            hits = c.execute("select rule, hit_count, first_hit_at, last_hit_at, details from case_rule_hits "
                             "where case_id = %s order by first_hit_at", (case_id,)).fetchall()
            report = c.execute("select id, body, low_quality, as_of, created_at from reports where case_id = %s "
                               "order by created_at desc limit 1", (case_id,)).fetchone()
            run = c.execute("select id, status, provider, model, started_at, latency_ms, tool_calls, input_tokens, "
                            "output_tokens, cost_micro_usd, error from agent_runs where case_id = %s "
                            "order by started_at desc limit 1", (case_id,)).fetchone()
            decisions = c.execute("select id, decision, analyst, note, created_at from decisions "
                                  "where case_id = %s order by created_at", (case_id,)).fetchall()
            stale = None
            if report is not None:
                # Ledger activity the report hasn't seen: the detector's copy of events after the report's as_of.
                (n,) = c.execute("select count(distinct transfer_id) from det_entries "
                                 "where account_id = %s and posted_at > %s", (case[1], report[3])).fetchone()
                stale = {"stale": n > 0, "new_transfers": n}
        return {
            "id": str(case[0]), "account_id": str(case[1]), "status": case[2], "risk_score": case[3],
            "flags": case[4], "opened_at": _iso(case[5]), "updated_at": _iso(case[6]),
            "last_activity_at": _iso(case[7]),
            "rule_hits": [{"rule": h[0], "hit_count": h[1], "first_hit_at": _iso(h[2]), "last_hit_at": _iso(h[3]),
                           "details": h[4]} for h in hits],
            "report": None if report is None else {"id": str(report[0]), **report[1], "low_quality": report[2],
                                                   "created_at": _iso(report[4])},
            "stale": stale["stale"] if stale else False,
            "new_transfers_since_report": stale["new_transfers"] if stale else 0,
            "latest_run": None if run is None else {
                "id": str(run[0]), "status": run[1], "provider": run[2], "model": run[3], "started_at": _iso(run[4]),
                "latency_ms": run[5], "tool_calls": run[6], "input_tokens": run[7], "output_tokens": run[8],
                "cost_micro_usd": run[9], "error": run[10]},
            "decisions": [{"id": str(d[0]), "decision": d[1], "analyst": d[2], "note": d[3],
                           "created_at": _iso(d[4])} for d in decisions],
        }

    @app.post("/cases/{case_id}/rerun", status_code=202)
    def rerun(case_id: UUID) -> dict[str, Any]:
        with connect(db) as c:
            row = c.execute("update cases set status = 'OPEN', updated_at = now(), "
                            "last_activity_at = least(last_activity_at, now()), version = version + 1 "
                            "where id = %s and status in ('PENDING_REVIEW', 'ESCALATED') returning status",
                            (case_id,)).fetchone()
            if row is None:
                exists = c.execute("select status from cases where id = %s", (case_id,)).fetchone()
                if exists is None:
                    raise ApiError(404, "case_not_found", f"case {case_id} does not exist")
                raise ApiError(409, "invalid_transition", f"a case in {exists[0]} cannot be re-run")
        return {"id": str(case_id), "status": "OPEN", "detail": "queued for the agent"}

    @app.post("/cases/{case_id}/decision")
    def decide(case_id: UUID, body: DecisionIn,
               idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)) -> JSONResponse:
        """Human decision. Idempotent on Idempotency-Key: the decision id (and so the ledger approval_ref) is
        derived from (case, key), so a retried or double-clicked FREEZE always maps to one ledger freeze."""
        decision_id = uuid5(DECISION_NS, f"{case_id}:{idempotency_key}")
        with connect(db) as c, c.transaction():
            case = c.execute("select account_id, status from cases where id = %s for update", (case_id,)).fetchone()
            if case is None:
                raise ApiError(404, "case_not_found", f"case {case_id} does not exist")
            prior = c.execute("select id, case_id, decision, analyst, note, ledger_response from decisions "
                              "where idempotency_key = %s", (idempotency_key,)).fetchone()
            if prior is not None:
                if (prior[1], prior[2], prior[3], prior[4]) != (case_id, body.decision, body.analyst, body.note):
                    raise ApiError(422, "idempotency_key_reused",
                                   "Idempotency-Key was already used for a different decision")
                return JSONResponse(_decision_out(prior[0], case_id, body, case[1], prior[5], replayed=True))
            account_id, status = case
            if status not in DECIDABLE or (status == "ESCALATED" and body.decision == "ESCALATE"):
                raise ApiError(409, "invalid_transition", f"cannot {body.decision} a case in {status}")
            ledger_response = None
            if body.decision == "FREEZE":
                try:
                    ledger_response = app.state.admin.freeze(
                        str(account_id), reason=f"case {case_id}: {body.note}"[:500], actor=body.analyst,
                        approval_ref=str(decision_id))
                except LedgerAdminError as e:
                    # Nothing recorded: the transaction rolls back and the analyst can retry with the same key.
                    raise ApiError(502 if e.status >= 500 else 409, f"ledger_{e.code}", e.detail) from None
            new_status = NEXT_STATUS[body.decision]
            c.execute("insert into decisions (id, case_id, decision, analyst, note, idempotency_key, ledger_response) "
                      "values (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                      (decision_id, case_id, body.decision, body.analyst, body.note, idempotency_key,
                       json.dumps(ledger_response) if ledger_response is not None else None))
            c.execute("update cases set status = %s, updated_at = now(), version = version + 1 where id = %s",
                      (new_status, case_id))
        return JSONResponse(_decision_out(decision_id, case_id, body, new_status, ledger_response, replayed=False),
                            status_code=201)

    return app


def _decision_out(decision_id, case_id, body: DecisionIn, status: str, ledger_response, replayed: bool) -> dict:
    return {"id": str(decision_id), "case_id": str(case_id), "decision": body.decision, "analyst": body.analyst,
            "note": body.note, "case_status": status, "ledger_response": ledger_response, "replayed": replayed}


app = create_app()

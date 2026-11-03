"""Cases API: listing, stale check, rerun, and the idempotent human decision endpoint."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.ledger_admin import LedgerAdminError
from app.api.ledger_read import LedgerReadError
from app.api.main import create_app
from app.config import Settings
from app.detector.rules import STRUCTURING, VELOCITY, Hit
from app.detector.store import attach_hit
from conftest import T0


class FakeAdmin:
    def __init__(self, fail: LedgerAdminError | None = None):
        self.calls: list[dict] = []
        self.fail = fail

    def freeze(self, account_id, *, reason, actor, approval_ref):
        self.calls.append({"account_id": account_id, "actor": actor, "approval_ref": approval_ref})
        if self.fail:
            raise self.fail
        return {"account_id": account_id, "status": "FROZEN", "approval_ref": approval_ref, "replayed": False}


class FakeReader:
    """READONLY ledger stand-in: a transfer graph keyed by account."""

    def __init__(self):
        self.transfers: list[dict] = []
        self.calls: list[str] = []

    def pay(self, src, dst, amount=100_000, kind="TRANSFER"):
        t = {"id": str(uuid4()), "type": kind, "from_account_id": str(src), "to_account_id": str(dst),
             "amount_minor": amount, "currency": "INR", "memo": None, "created_at": T0.isoformat()}
        self.transfers.append(t)
        return t["id"]

    def get(self, path, params=None):
        self.calls.append(path)
        parts = path.strip("/").split("/")
        if parts[1] == "transfers":
            t = next((t for t in self.transfers if t["id"] == parts[2]), None)
            if t is None:
                raise LedgerReadError(404, "transfer_not_found", "no such transfer")
            return {"transfer": t, "as_of": T0.isoformat()}
        acct = parts[2]
        if len(parts) == 3:
            return {"id": acct, "owner_name": "Asha", "status": "ACTIVE", "currency": "INR", "is_system": False,
                    "created_at": T0.isoformat(), "as_of": T0.isoformat()}
        if parts[3] == "balance":
            return {"account_id": acct, "balance_minor": 4_200_00, "currency": "INR", "as_of": T0.isoformat()}
        items = [t for t in self.transfers if acct in (t["from_account_id"], t["to_account_id"])]
        return {"items": items[:(params or {}).get("limit", 50)], "next_cursor": None, "as_of": T0.isoformat()}


@pytest.fixture()
def admin():
    return FakeAdmin()


@pytest.fixture()
def reader():
    return FakeReader()


@pytest.fixture()
def client(db_url, admin, reader, conn):
    with TestClient(create_app(Settings(agent_database_url=db_url), admin=admin, reader=reader)) as c:
        yield c


def seed(conn, status="PENDING_REVIEW", risk=0.8, as_of=T0):
    account = uuid4()
    case_id = attach_hit(conn, Hit(account, STRUCTURING, T0, {"count": 3}))
    attach_hit(conn, Hit(account, VELOCITY, T0, {"count": 9}))
    run_id = uuid4()
    conn.execute("insert into agent_runs (id, case_id, status, provider, model) values (%s, %s, 'SUCCEEDED', "
                 "'fake', 'fake')", (run_id, case_id))
    body = {"verdict": "RECOMMEND_FREEZE", "risk_score": risk, "confidence": 0.9, "summary": "s", "evidence": [],
            "red_flags": [], "injection_detected": False, "as_of": as_of.isoformat()}
    conn.execute("insert into reports (id, case_id, agent_run_id, verdict, risk_score, confidence, injection_detected, "
                 "as_of, body) values (%s, %s, %s, 'RECOMMEND_FREEZE', %s, 0.9, false, %s, %s::jsonb)",
                 (uuid4(), case_id, run_id, risk, as_of, json.dumps(body)))
    conn.execute("update cases set status = %s, risk_score = %s where id = %s", (status, risk, case_id))
    return case_id, account


def decide(client, case_id, key, decision="FREEZE", note="mule pattern confirmed", analyst="priya@bank"):
    return client.post(f"/cases/{case_id}/decision", headers={"Idempotency-Key": key},
                       json={"decision": decision, "analyst": analyst, "note": note})


def test_list_filters_and_sorts_by_risk(client, conn):
    low, _ = seed(conn, risk=0.2)
    high, _ = seed(conn, risk=0.9)
    seed(conn, status="CLEARED", risk=0.99)
    body = client.get("/cases", params={"status": "PENDING_REVIEW", "min_risk": 0.1}).json()["cases"]
    assert [c["id"] for c in body] == [str(high), str(low)]
    assert body[0]["rules"] == [STRUCTURING, VELOCITY]
    assert client.get("/cases", params={"status": "BOGUS"}).status_code == 422


def test_case_detail_and_stale_report(client, conn):
    case_id, account = seed(conn, as_of=T0)
    detail = client.get(f"/cases/{case_id}").json()
    assert detail["stale"] is False and detail["report"]["verdict"] == "RECOMMEND_FREEZE"
    assert len(detail["rule_hits"]) == 2
    for minutes in (5, 6):  # two transfers after the report's as_of
        conn.execute("insert into det_entries values (%s, %s, %s, %s, 'DEBIT', 100, 'TRANSFER', false, %s, %s)",
                     (uuid4(), uuid4(), account, uuid4(), T0 - timedelta(days=30), T0 + timedelta(minutes=minutes)))
    detail = client.get(f"/cases/{case_id}").json()
    assert detail["stale"] is True and detail["new_transfers_since_report"] == 2
    assert client.get(f"/cases/{uuid4()}").status_code == 404


def test_freeze_decision_calls_ledger_once_with_decision_id_as_approval_ref(client, conn, admin):
    case_id, account = seed(conn)
    r = decide(client, case_id, "click-1")
    assert r.status_code == 201
    body = r.json()
    assert body["case_status"] == "FROZEN"
    assert admin.calls == [{"account_id": str(account), "actor": "priya@bank", "approval_ref": body["id"]}]
    assert conn.execute("select status from cases where id = %s", (case_id,)).fetchone()[0] == "FROZEN"


def test_double_click_is_idempotent(client, conn, admin):
    case_id, _ = seed(conn)
    first = decide(client, case_id, "click-2")
    again = decide(client, case_id, "click-2")
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
    assert len(admin.calls) == 1
    assert conn.execute("select count(*) from decisions where case_id = %s", (case_id,)).fetchone()[0] == 1


def test_same_key_different_decision_is_rejected(client, conn):
    case_id, _ = seed(conn)
    assert decide(client, case_id, "click-3", decision="CLEAR").status_code == 201
    r = decide(client, case_id, "click-3", decision="FREEZE")
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"


@pytest.mark.parametrize("status", ["OPEN", "INVESTIGATING", "AUTO_CLEARED", "CLEARED", "FROZEN"])
def test_decisions_only_from_pending_review(client, conn, admin, status):
    case_id, _ = seed(conn, status=status)
    r = decide(client, case_id, f"k-{status}")
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"
    assert admin.calls == []


def test_escalated_case_can_be_resolved_but_not_re_escalated(client, conn):
    case_id, _ = seed(conn)
    assert decide(client, case_id, "e1", decision="ESCALATE").json()["case_status"] == "ESCALATED"
    assert decide(client, case_id, "e2", decision="ESCALATE").status_code == 409
    assert decide(client, case_id, "e3", decision="CLEAR").json()["case_status"] == "CLEARED"
    assert decide(client, case_id, "e4", decision="FREEZE").status_code == 409  # terminal now


def test_ledger_failure_records_nothing(client, conn, db_url):
    failing = FakeAdmin(fail=LedgerAdminError(503, "ledger_unavailable", "down"))
    case_id, _ = seed(conn)
    with TestClient(create_app(Settings(agent_database_url=db_url), admin=failing, reader=FakeReader())) as c:
        r = decide(c, case_id, "click-4")
    assert r.status_code == 502
    assert conn.execute("select count(*) from decisions where case_id = %s", (case_id,)).fetchone()[0] == 0
    assert conn.execute("select status from cases where id = %s", (case_id,)).fetchone()[0] == "PENDING_REVIEW"


def test_decision_validation(client, conn):
    case_id, _ = seed(conn)
    assert client.post(f"/cases/{case_id}/decision", json={"decision": "FREEZE", "analyst": "a", "note": "n"}
                       ).status_code == 422  # missing Idempotency-Key
    assert decide(client, case_id, "v1", note="").status_code == 422
    assert decide(client, case_id, "v2", decision="DELETE").status_code == 422


def test_account_lookup_merges_ledger_and_cases(client, conn, reader):
    case_id, account = seed(conn)
    reader.pay(account, uuid4())
    body = client.get(f"/accounts/{account}").json()
    assert body["balance_minor"] == 420_000 and body["account"]["status"] == "ACTIVE"
    assert len(body["recent_transfers"]) == 1
    assert [c["id"] for c in body["cases"]] == [str(case_id)]
    assert body["as_of"]


def test_transfer_detail_and_ledger_404(client, reader):
    tid = reader.pay(uuid4(), uuid4())
    assert client.get(f"/transfers/{tid}").json()["transfer"]["id"] == tid
    r = client.get(f"/transfers/{uuid4()}")
    assert r.status_code == 404 and r.json()["code"] == "transfer_not_found"


def test_case_graph_is_bounded_and_marks_the_flagged_account(client, conn, reader):
    case_id, account = seed(conn)
    a, b, c = uuid4(), uuid4(), uuid4()
    reader.pay(uuid4(), account, kind="DEPOSIT")  # funding edge: shown, not expanded
    reader.pay(account, a)
    reader.pay(a, b)
    reader.pay(b, c)  # 3 hops out: beyond hops=2
    reader.pay(a, account)  # cycle back (keeps a at hop 1, so b is hop 2 and c hop 3)
    graph = client.get(f"/cases/{case_id}/graph").json()
    ids = {n["id"]: n for n in graph["nodes"]}
    assert ids[str(account)]["is_root"] and ids[str(account)]["has_open_case"]
    assert str(a) in ids and str(b) in ids and str(c) not in ids
    assert sum(n["is_system"] for n in graph["nodes"]) == 1
    for _ in range(60):
        reader.pay(account, uuid4())
    big = client.get(f"/cases/{case_id}/graph").json()
    assert len(big["nodes"]) <= 40 and big["truncated"] is True


def test_rerun(client, conn):
    case_id, _ = seed(conn)
    assert client.post(f"/cases/{case_id}/rerun").status_code == 202
    assert conn.execute("select status from cases where id = %s", (case_id,)).fetchone()[0] == "OPEN"
    assert client.post(f"/cases/{case_id}/rerun").status_code == 409  # already queued

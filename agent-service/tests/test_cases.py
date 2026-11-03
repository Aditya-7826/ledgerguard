"""Exactly-once processing and the one-active-case-per-account invariant."""

from datetime import timedelta
from uuid import uuid4

from app.detector.rules import RAPID_FANOUT, VELOCITY, Hit
from app.detector.store import attach_hit
from conftest import T0, Ledger

MIN = timedelta(minutes=1)


def test_duplicate_event_is_applied_once(ledger: Ledger, conn):
    a = ledger.account()
    event = {
        "event_id": str(uuid4()), "event_type": "EntryPosted", "entry_id": str(uuid4()),
        "transfer_id": str(uuid4()), "account_id": str(a), "counterparty_account_id": str(uuid4()),
        "direction": "CREDIT", "amount_minor": 1000, "currency": "INR", "transfer_type": "TRANSFER",
        "memo": None, "account_created_at": (T0 - timedelta(days=90)).isoformat(),
        "account_is_system": False, "counterparty_is_system": False,
        "counterparty_created_at": T0.isoformat(), "posted_at": T0.isoformat(),
    }
    from app.detector.store import process_event

    assert process_event(conn, event, ledger.cfg) == []
    assert process_event(conn, event, ledger.cfg) is None  # duplicate delivery
    assert conn.execute("select count(*) from det_entries where account_id = %s", (a,)).fetchone()[0] == 1
    assert conn.execute("select count(*) from processed_events").fetchone()[0] == 1


def test_new_hits_attach_to_the_single_open_case(ledger: Ledger, conn):
    a = ledger.account()
    attach_hit(conn, Hit(a, VELOCITY, T0, {"n": 1}))
    attach_hit(conn, Hit(a, VELOCITY, T0 + MIN, {"n": 2}))
    attach_hit(conn, Hit(a, RAPID_FANOUT, T0 + 2 * MIN, {}))

    cases = conn.execute("select id from cases where account_id = %s", (a,)).fetchall()
    assert len(cases) == 1
    hits = dict(conn.execute(
        "select rule, hit_count from case_rule_hits where case_id = %s", (cases[0][0],)).fetchall())
    assert hits == {VELOCITY: 2, RAPID_FANOUT: 1}


def test_a_closed_case_lets_a_new_one_open(ledger: Ledger, conn):
    a = ledger.account()
    first = attach_hit(conn, Hit(a, VELOCITY, T0, {}))
    conn.execute("update cases set status = 'CLEARED' where id = %s", (first,))
    second = attach_hit(conn, Hit(a, VELOCITY, T0 + MIN, {}))
    assert second != first
    active = conn.execute(
        "select count(*) from cases where account_id = %s and status = 'OPEN'", (a,)).fetchone()[0]
    assert active == 1


def test_system_account_never_gets_a_case(ledger: Ledger, conn):
    for i in range(30):
        ledger.deposit(ledger.account(), 1_000, T0 + i * MIN)
    from conftest import SYSTEM

    assert conn.execute("select count(*) from cases where account_id = %s", (SYSTEM,)).fetchone()[0] == 0

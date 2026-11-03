from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

from app.config import RuleConfig
from app.db import connect, migrate
from app.detector.store import process_event

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
SYSTEM = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        url = pg.get_connection_url()
        migrate(url)
        yield url


@pytest.fixture()
def conn(db_url: str) -> Iterator[psycopg.Connection]:
    c = connect(db_url)
    c.execute(
        "truncate processed_events, det_entries, det_transfers, case_rule_hits, decisions, reports, "
        "agent_runs, cases"
    )
    yield c
    c.close()


class Ledger:
    """Feeds the detector the same three events the ledger emits for one transfer."""

    def __init__(self, conn: psycopg.Connection, cfg: RuleConfig | None = None):
        self.conn = conn
        self.cfg = cfg or RuleConfig()
        self.created: dict[UUID, datetime] = {}

    def account(self, age: timedelta = timedelta(days=90), now: datetime = T0) -> UUID:
        a = uuid4()
        self.created[a] = now - age
        return a

    def _created(self, a: UUID) -> datetime:
        return T0 - timedelta(days=365) if a == SYSTEM else self.created[a]

    def transfer(self, src: UUID, dst: UUID, amount: int, at: datetime, kind: str = "TRANSFER") -> list:
        tid = uuid4()
        hits: list = []
        for account, cp, direction in ((src, dst, "DEBIT"), (dst, src, "CREDIT")):
            hits += self.send({
                "event_id": str(uuid4()), "event_type": "EntryPosted", "entry_id": str(uuid4()),
                "transfer_id": str(tid), "account_id": str(account), "counterparty_account_id": str(cp),
                "direction": direction, "amount_minor": amount, "currency": "INR", "transfer_type": kind,
                "memo": None, "account_created_at": self._created(account).isoformat(),
                "account_is_system": account == SYSTEM, "counterparty_is_system": cp == SYSTEM,
                "counterparty_created_at": self._created(cp).isoformat(), "posted_at": at.isoformat(),
            })
        hits += self.send({
            "event_id": str(uuid4()), "event_type": "TransferPosted", "transfer_id": str(tid), "type": kind,
            "from_account_id": str(src), "to_account_id": str(dst), "amount_minor": amount, "currency": "INR",
            "memo": None, "from_is_system": src == SYSTEM, "to_is_system": dst == SYSTEM,
            "from_account_created_at": self._created(src).isoformat(),
            "to_account_created_at": self._created(dst).isoformat(), "posted_at": at.isoformat(),
        })
        return hits

    def deposit(self, dst: UUID, amount: int, at: datetime) -> list:
        return self.transfer(SYSTEM, dst, amount, at, kind="DEPOSIT")

    def send(self, event: dict) -> list:
        return process_event(self.conn, event, self.cfg) or []

    def rules_for(self, account: UUID) -> set[str]:
        rows = self.conn.execute(
            "select h.rule from case_rule_hits h join cases c on c.id = h.case_id where c.account_id = %s",
            (account,),
        ).fetchall()
        return {r[0] for r in rows}


@pytest.fixture()
def ledger(conn: psycopg.Connection) -> Ledger:
    return Ledger(conn)

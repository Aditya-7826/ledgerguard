"""Event processing: dedupe, record, evaluate rules, attach hits to cases. One DB transaction per event."""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg

from app.config import RuleConfig
from app.detector import rules
from app.detector.rules import Hit

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("OPEN", "INVESTIGATING", "PENDING_REVIEW", "ESCALATED")


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def process_event(conn: psycopg.Connection, event: dict[str, Any], cfg: RuleConfig) -> list[Hit] | None:
    """Applies one ledger event exactly once. Returns the hits, or None if the event was a duplicate.

    The processed_events marker, the detector's copy of the data and the case/rule-hit writes commit
    together, so a crash at any point either applies everything or nothing (at-least-once → once).
    """
    event_id = UUID(event["event_id"])
    event_type = event["event_type"]
    with conn.transaction():
        cur = conn.execute(
            "insert into processed_events (event_id, event_type) values (%s, %s) on conflict do nothing",
            (event_id, event_type),
        )
        if cur.rowcount == 0:
            return None
        if event_type == "EntryPosted":
            hits = _on_entry(conn, event, cfg)
        elif event_type == "TransferPosted":
            hits = _on_transfer(conn, event, cfg)
        else:
            hits = []
        for hit in hits:
            attach_hit(conn, hit)
    return hits


def _on_entry(conn: psycopg.Connection, e: dict[str, Any], cfg: RuleConfig) -> list[Hit]:
    account = UUID(e["account_id"])
    t = _ts(e["posted_at"])
    created_at = _ts(e["account_created_at"])
    conn.execute(
        """
        insert into det_entries (entry_id, transfer_id, account_id, counterparty_account_id, direction,
                                 amount_minor, transfer_type, counterparty_is_system, account_created_at, posted_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict do nothing
        """,
        (UUID(e["entry_id"]), UUID(e["transfer_id"]), account, UUID(e["counterparty_account_id"]),
         e["direction"], int(e["amount_minor"]), e["transfer_type"], bool(e["counterparty_is_system"]),
         created_at, t),
    )
    if e.get("account_is_system"):
        return []  # funding accounts are expected to fan out to everyone

    candidates: list[Hit | None] = [rules.velocity(conn, cfg, account, e["direction"], t)]
    if e["direction"] == "DEBIT":
        candidates.append(rules.structuring(conn, cfg, account, int(e["amount_minor"]), t))
        candidates.append(rules.rapid_fanout(conn, cfg, account, t))
    else:
        candidates.append(rules.fan_in_new_account(conn, cfg, account, created_at, t))
    return [h for h in candidates if h is not None]


def _on_transfer(conn: psycopg.Connection, e: dict[str, Any], cfg: RuleConfig) -> list[Hit]:
    if e.get("from_is_system") or e.get("to_is_system"):
        return []  # deposits aren't customer-to-customer edges
    transfer_id = UUID(e["transfer_id"])
    conn.execute(
        "insert into det_transfers (transfer_id, from_account_id, to_account_id, amount_minor, posted_at) "
        "values (%s, %s, %s, %s, %s) on conflict do nothing",
        (transfer_id, UUID(e["from_account_id"]), UUID(e["to_account_id"]), int(e["amount_minor"]),
         _ts(e["posted_at"])),
    )
    return rules.round_trip(conn, cfg, transfer_id)


def attach_hit(conn: psycopg.Connection, hit: Hit) -> UUID:
    """Opens a case for the account or reuses its single active one, then upserts the rule hit."""
    row = conn.execute(
        """
        insert into cases (id, account_id, status, opened_at, updated_at, last_activity_at)
        values (%s, %s, 'OPEN', now(), now(), %s)
        on conflict (account_id) where status in ('OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'ESCALATED')
        do nothing
        returning id
        """,
        (uuid4(), hit.account_id, hit.at),
    ).fetchone()
    if row is not None:
        case_id = row[0]
        log.info("opened case %s for account %s (%s)", case_id, hit.account_id, hit.rule)
    else:
        case_id = conn.execute(
            "update cases set updated_at = now(), last_activity_at = greatest(last_activity_at, %s) "
            "where account_id = %s and status = any(%s) returning id",
            (hit.at, hit.account_id, list(ACTIVE_STATUSES)),
        ).fetchone()[0]
    conn.execute(
        """
        insert into case_rule_hits (id, case_id, rule, first_hit_at, last_hit_at, hit_count, details)
        values (%s, %s, %s, %s, %s, 1, %s::jsonb)
        on conflict (case_id, rule) do update
           set last_hit_at = greatest(case_rule_hits.last_hit_at, excluded.last_hit_at),
               hit_count = case_rule_hits.hit_count + 1,
               details = excluded.details
        """,
        (uuid4(), case_id, hit.rule, hit.at, hit.at, json.dumps(hit.details)),
    )
    return case_id

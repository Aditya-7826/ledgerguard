"""The five detection rules, as SQL over the detector's own tables.

Every rule is evaluated "as of" the triggering event's ``posted_at`` (ledger time), never wall-clock
time, so replays and tests are deterministic. Windows are half-open: (t - window, t].
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from app.config import RuleConfig

VELOCITY = "velocity"
STRUCTURING = "structuring"
FAN_IN_NEW_ACCOUNT = "fan_in_new_account"
RAPID_FANOUT = "rapid_fanout_after_receiving"
ROUND_TRIP = "round_trip"


@dataclass(frozen=True)
class Hit:
    account_id: UUID
    rule: str
    at: datetime
    details: dict[str, Any] = field(default_factory=dict)


def velocity(conn: psycopg.Connection, cfg: RuleConfig, account: UUID, direction: str, t: datetime) -> Hit | None:
    limit = cfg.velocity_out_count if direction == "DEBIT" else cfg.velocity_in_count
    lo = t - cfg.window(cfg.velocity_window_s)
    (n,) = conn.execute(
        "select count(*) from det_entries where account_id = %s and direction = %s "
        "and posted_at > %s and posted_at <= %s",
        (account, direction, lo, t),
    ).fetchone()
    if n >= limit:
        return Hit(account, VELOCITY, t, {
            "direction": "outgoing" if direction == "DEBIT" else "incoming",
            "count": n, "threshold": limit, "window_s": cfg.velocity_window_s,
        })
    return None


def structuring(conn: psycopg.Connection, cfg: RuleConfig, account: UUID, amount: int, t: datetime) -> Hit | None:
    lo_amt = int(cfg.structuring_threshold_minor * cfg.structuring_band)
    hi_amt = cfg.structuring_threshold_minor  # exclusive: exactly-at-threshold isn't "just under"
    if not (lo_amt <= amount < hi_amt):
        return None
    lo = t - cfg.window(cfg.structuring_window_s)
    n, total, ids = conn.execute(
        "select count(*), coalesce(sum(amount_minor), 0)::bigint, array_agg(transfer_id::text order by posted_at) "
        "from det_entries where account_id = %s and direction = 'DEBIT' "
        "and amount_minor >= %s and amount_minor < %s and posted_at > %s and posted_at <= %s",
        (account, lo_amt, hi_amt, lo, t),
    ).fetchone()
    if n >= cfg.structuring_count:
        return Hit(account, STRUCTURING, t, {
            "count": n, "total_minor": int(total), "band_minor": [lo_amt, hi_amt],
            "threshold": cfg.structuring_count, "window_s": cfg.structuring_window_s, "transfer_ids": ids,
        })
    return None


def fan_in_new_account(conn: psycopg.Connection, cfg: RuleConfig, account: UUID,
                       account_created_at: datetime, t: datetime) -> Hit | None:
    age = t - account_created_at
    if age >= cfg.window(cfg.fan_in_max_account_age_s):
        return None
    lo = t - cfg.window(cfg.fan_in_window_s)
    (senders,) = conn.execute(
        "select count(distinct counterparty_account_id) from det_entries "
        "where account_id = %s and direction = 'CREDIT' and not counterparty_is_system "
        "and posted_at > %s and posted_at <= %s",
        (account, lo, t),
    ).fetchone()
    if senders >= cfg.fan_in_senders:
        return Hit(account, FAN_IN_NEW_ACCOUNT, t, {
            "distinct_senders": senders, "threshold": cfg.fan_in_senders,
            "account_age_s": round(age.total_seconds() * cfg.time_scale), "window_s": cfg.fan_in_window_s,
        })
    return None


def rapid_fanout(conn: psycopg.Connection, cfg: RuleConfig, account: UUID, t: datetime) -> Hit | None:
    """A credit of at least the minimum, followed within the window by debits worth ≥ ratio of it."""
    lo = t - cfg.window(cfg.fanout_window_s)
    credits = conn.execute(
        "select transfer_id, amount_minor, posted_at from det_entries "
        "where account_id = %s and direction = 'CREDIT' and amount_minor >= %s "
        "and posted_at > %s and posted_at <= %s order by posted_at limit 50",
        (account, cfg.fanout_min_credit_minor, lo, t),
    ).fetchall()
    for transfer_id, credit_amount, credit_at in credits:
        (out,) = conn.execute(
            # sum(bigint) is numeric in Postgres; cast back so amounts stay Python ints, never Decimal/float.
            "select coalesce(sum(amount_minor), 0)::bigint from det_entries where account_id = %s "
            "and direction = 'DEBIT' and posted_at >= %s and posted_at <= %s",
            (account, credit_at, t),
        ).fetchone()
        if out >= cfg.fanout_ratio * credit_amount:
            return Hit(account, RAPID_FANOUT, t, {
                "credit_transfer_id": str(transfer_id), "credit_minor": credit_amount,
                "outflow_minor": out, "ratio": round(out / credit_amount, 4),
                "threshold_ratio": cfg.fanout_ratio, "window_s": cfg.fanout_window_s,
                "elapsed_s": round((t - credit_at).total_seconds() * cfg.time_scale),
            })
    return None


def round_trip(conn: psycopg.Connection, cfg: RuleConfig, transfer_id: UUID) -> list[Hit]:
    """Does the new edge a→b close a directed cycle b ⇝ a (length min..max hops) within the window?

    Bounded search with a visited set (no node repeats), edges ≥ minimum amount, all edges inside one
    window, and money retained: smallest hop ≥ retention × largest hop. Edge order isn't required, so
    events arriving out of order across partitions still close the cycle.
    """
    window = cfg.window(cfg.round_trip_window_s)
    row = conn.execute(
        """
        with recursive walk (node, depth, path, edges, min_amt, max_amt, min_t, max_t) as (
            select e.to_account_id, 1, array[e.from_account_id, e.to_account_id], array[e.transfer_id],
                   e.amount_minor, e.amount_minor, e.posted_at, e.posted_at
              from det_transfers e
             where e.transfer_id = %(tid)s and e.amount_minor >= %(min_amt)s
            union all
            select t.to_account_id, w.depth + 1, w.path || t.to_account_id, w.edges || t.transfer_id,
                   least(w.min_amt, t.amount_minor), greatest(w.max_amt, t.amount_minor),
                   least(w.min_t, t.posted_at), greatest(w.max_t, t.posted_at)
              from walk w
              join det_transfers t on t.from_account_id = w.node
             where w.depth < %(max_hops)s
               and w.node <> w.path[1]
               and t.amount_minor >= %(min_amt)s
               and t.transfer_id <> all(w.edges)
               and (t.to_account_id = w.path[1] or t.to_account_id <> all(w.path))
               and greatest(w.max_t, t.posted_at) - least(w.min_t, t.posted_at) <= %(window)s
        )
        select path, edges, depth, min_amt, max_amt
          from walk
         where node = path[1] and depth >= %(min_hops)s
           and min_amt >= %(retention)s * max_amt
         order by depth
         limit 1
        """,
        {"tid": transfer_id, "min_amt": cfg.round_trip_min_amount_minor, "max_hops": cfg.round_trip_max_hops,
         "min_hops": cfg.round_trip_min_hops, "window": window, "retention": cfg.round_trip_min_retention},
    ).fetchone()
    if row is None:
        return []
    path, edges, depth, min_amt, max_amt = row
    members = path[:-1]  # last element repeats the start
    at = conn.execute("select max(posted_at) from det_transfers where transfer_id = any(%s)", (edges,)).fetchone()[0]
    details = {
        "cycle": [str(a) for a in path], "transfer_ids": [str(e) for e in edges], "hops": depth,
        "min_minor": min_amt, "max_minor": max_amt, "window_s": cfg.round_trip_window_s,
    }
    return [Hit(m, ROUND_TRIP, at, details) for m in members]

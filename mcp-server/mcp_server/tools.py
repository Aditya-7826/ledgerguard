"""The six READ-ONLY tools. Every result carries ``as_of``; every failure is a structured error, never a
raw exception; customer free text (memos, owner names) is wrapped as ``{"untrusted_text": ...}``."""

import re
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from mcp_server.ledger import LedgerClient, ToolError, inr, now_iso, shape_transfer, untrusted

MAX_HOPS = 4
MAX_WINDOW_HOURS = 168
NODE_CAP = 200
EDGE_CAP = 1000
PAGES_PER_NODE = 3
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


def _uuid(value: Any, name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ToolError("invalid_argument", f"{name} must be a UUID like 3f2b8c1e-...; got {str(value)[:64]!r}") from None


def _iso(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _ISO.match(value):
        raise ToolError("invalid_argument", f"{name} must be an ISO-8601 timestamp with offset, e.g. 2026-11-03T10:00:00Z")
    return value


def _int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("invalid_argument", f"{name} must be an integer between {lo} and {hi}")
    if not lo <= value <= hi:
        raise ToolError("invalid_argument", f"{name} must be between {lo} and {hi}; got {value}")
    return value


def error(e: ToolError) -> dict[str, Any]:
    return {"error": {"code": e.code, "message": e.message}, "as_of": now_iso()}


class LedgerTools:
    def __init__(self, ledger: LedgerClient):
        self.ledger = ledger

    async def get_account(self, account_id: str) -> dict[str, Any]:
        a = await self.ledger.get(f"/v1/accounts/{_uuid(account_id, 'account_id')}")
        return {"account_id": a["id"], "owner_name": untrusted(a["owner_name"]), "currency": a["currency"],
                "status": a["status"], "is_system": a["is_system"], "created_at": a["created_at"], "as_of": a["as_of"]}

    async def get_balance(self, account_id: str, as_of: str | None = None) -> dict[str, Any]:
        b = await self.ledger.get(f"/v1/accounts/{_uuid(account_id, 'account_id')}/balance",
                                  {"as_of": _iso(as_of, "as_of")})
        return {"account_id": b["account_id"], "balance_minor": b["balance_minor"],
                "balance_display": inr(b["balance_minor"]), "currency": b["currency"], "as_of": b["as_of"]}

    async def list_transfers(self, account_id: str, from_time: str | None = None, to_time: str | None = None,
                             cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        acct = _uuid(account_id, "account_id")
        page = await self.ledger.get(f"/v1/accounts/{acct}/transfers", {
            "from": _iso(from_time, "from_time"), "to": _iso(to_time, "to_time"),
            "cursor": cursor[:512] if isinstance(cursor, str) and cursor else None,
            "limit": _int(limit, "limit", 1, 100)})
        return {"account_id": acct, "from_time": page["from"], "to_time": page["to"],
                "transfers": [shape_transfer(t) for t in page["items"]], "next_cursor": page["next_cursor"],
                "as_of": page["as_of"]}

    async def get_counterparties(self, account_id: str, from_time: str | None = None,
                                 to_time: str | None = None) -> dict[str, Any]:
        acct = _uuid(account_id, "account_id")
        page = await self.ledger.get(f"/v1/accounts/{acct}/counterparties",
                                     {"from": _iso(from_time, "from_time"), "to": _iso(to_time, "to_time")})
        rows = [{**c, "sent_display": inr(c["sent_minor"]), "received_display": inr(c["received_minor"])}
                for c in page["counterparties"]]
        return {"account_id": acct, "from_time": page["from"], "to_time": page["to"], "counterparties": rows,
                "truncated": page["truncated"], "as_of": page["as_of"]}

    async def get_transfer(self, transfer_id: str) -> dict[str, Any]:
        t = await self.ledger.get(f"/v1/transfers/{_uuid(transfer_id, 'transfer_id')}")
        shaped = shape_transfer(t["transfer"])
        shaped["entries"] = t["transfer"]["entries"]
        return {**shaped, "as_of": t["as_of"]}

    async def trace_money_flow(self, account_id: str, direction: Literal["out", "in"] = "out", max_hops: int = 2,
                               window_hours: int = 72) -> dict[str, Any]:
        """Bounded BFS over transfers. The visited set makes cycles safe; node/edge/page caps bound the
        work, and ``truncated`` tells the caller when a cap cut the graph short. Deposits (system funding)
        are shown as edges but never expanded, so the trace doesn't fan out to every customer."""
        root = _uuid(account_id, "account_id")
        if direction not in ("out", "in"):
            raise ToolError("invalid_argument", "direction must be 'out' or 'in'")
        max_hops = _int(max_hops, "max_hops", 1, MAX_HOPS)
        window_hours = _int(window_hours, "window_hours", 1, MAX_WINDOW_HOURS)
        to_time = datetime.now(UTC)
        from_time = to_time - timedelta(hours=window_hours)
        params = {"from": from_time.isoformat(), "to": to_time.isoformat(), "limit": 100}

        nodes: dict[str, int] = {root: 0}
        edges: dict[str, dict[str, Any]] = {}
        truncated = False
        as_of = None
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        while queue:
            node, hop = queue.popleft()
            if hop >= max_hops:
                continue
            cursor = None
            for page_no in range(PAGES_PER_NODE):
                page = await self.ledger.get(f"/v1/accounts/{node}/transfers", {**params, "cursor": cursor})
                as_of = as_of or page["as_of"]
                for t in page["items"]:
                    outgoing = t["from_account_id"] == node
                    if (direction == "out") != outgoing:
                        continue
                    if len(edges) >= EDGE_CAP and t["id"] not in edges:
                        truncated = True
                        continue
                    edges.setdefault(t["id"], shape_transfer(t))
                    if t["type"] == "DEPOSIT":
                        continue  # funding from the system account: don't walk into it
                    nxt = t["to_account_id"] if outgoing else t["from_account_id"]
                    if nxt in nodes:
                        continue  # visited (cycle or diamond)
                    if len(nodes) >= NODE_CAP:
                        truncated = True
                        continue
                    nodes[nxt] = hop + 1
                    queue.append((nxt, hop + 1))
                cursor = page["next_cursor"]
                if not cursor:
                    break
                if page_no == PAGES_PER_NODE - 1:
                    truncated = True
        return {
            "root_account_id": root, "direction": direction, "max_hops": max_hops, "window_hours": window_hours,
            "nodes": [{"account_id": a, "hop": h} for a, h in nodes.items()],
            "edges": list(edges.values()), "truncated": truncated, "as_of": as_of or now_iso(),
        }

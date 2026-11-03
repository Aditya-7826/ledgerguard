"""READONLY ledger reads for the UI (account lookup, transfer detail, money-flow graph).

Separate from ledger_admin.py on purpose: this key can only GET, the ADMIN key can only be used by the
decision endpoint.
"""

from collections import deque
from typing import Any, Protocol

import httpx


class LedgerReadError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class LedgerReader(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...


class HttpLedgerReader:
    def __init__(self, base_url: str, readonly_api_key: str, transport: httpx.BaseTransport | None = None):
        if not readonly_api_key:
            raise ValueError("LEDGER_READONLY_API_KEY must be set for the cases API")
        self._http = httpx.Client(base_url=base_url, headers={"X-API-Key": readonly_api_key}, timeout=15.0,
                                  transport=transport)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._http.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        except httpx.HTTPError as e:
            raise LedgerReadError(503, "ledger_unavailable", f"ledger unreachable: {type(e).__name__}") from None
        if resp.status_code == 200:
            return resp.json()
        try:
            body = resp.json()
        except ValueError:
            body = {}
        raise LedgerReadError(resp.status_code, body.get("code", "ledger_error"),
                              body.get("detail", f"ledger returned HTTP {resp.status_code}"))


def money_flow_graph(reader: LedgerReader, root: str, hops: int = 2, node_cap: int = 40,
                     edge_cap: int = 150) -> dict[str, Any]:
    """Accounts within `hops` transfers of the root (both directions) over the ledger's default 30-day
    window. Deposits are drawn as edges from the funding account but never expanded."""
    nodes: dict[str, int] = {root: 0}
    edges: dict[str, dict[str, Any]] = {}
    system: set[str] = set()
    truncated = False
    as_of = None
    queue: deque[tuple[str, int]] = deque([(root, 0)])
    while queue:
        node, hop = queue.popleft()
        page = reader.get(f"/v1/accounts/{node}/transfers", {"limit": 100})
        as_of = as_of or page["as_of"]
        if page.get("next_cursor"):
            truncated = True
        for t in page["items"]:
            src, dst = t["from_account_id"], t["to_account_id"]
            if t["type"] == "DEPOSIT":
                system.add(src)
            other = dst if src == node else src
            if other not in nodes:
                if len(nodes) >= node_cap:
                    truncated = True
                    continue
                nodes[other] = hop + 1
                if hop + 1 < hops and t["type"] != "DEPOSIT":
                    queue.append((other, hop + 1))
            if t["id"] not in edges:
                if len(edges) >= edge_cap:
                    truncated = True
                    continue
                edges[t["id"]] = {"id": t["id"], "source": src, "target": dst, "amount_minor": t["amount_minor"],
                                  "type": t["type"], "created_at": t["created_at"]}
    return {"root": root, "hops": hops, "truncated": truncated, "as_of": as_of,
            "nodes": [{"id": a, "hop": h, "is_root": a == root, "is_system": a in system} for a, h in nodes.items()],
            "edges": list(edges.values())}

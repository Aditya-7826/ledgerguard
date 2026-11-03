"""The ONLY use of the ledger ADMIN key in the whole system: freezing after a human FREEZE decision."""

from typing import Any, Protocol

import httpx


class LedgerAdminError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class LedgerAdmin(Protocol):
    def freeze(self, account_id: str, *, reason: str, actor: str, approval_ref: str) -> dict[str, Any]: ...


class HttpLedgerAdmin:
    def __init__(self, base_url: str, admin_api_key: str):
        if not admin_api_key:
            raise ValueError("LEDGER_ADMIN_API_KEY must be set for the cases API")
        self._http = httpx.Client(base_url=base_url, headers={"X-API-Key": admin_api_key}, timeout=15.0)

    def freeze(self, account_id: str, *, reason: str, actor: str, approval_ref: str) -> dict[str, Any]:
        # The ledger is idempotent on approval_ref, so retrying this call is always safe.
        last: Exception | None = None
        for _ in range(3):
            try:
                resp = self._http.post(f"/v1/admin/accounts/{account_id}/freeze",
                                       json={"reason": reason, "actor": actor, "approval_ref": approval_ref})
            except httpx.HTTPError as e:
                last = e
                continue
            if resp.status_code == 200:
                return resp.json()
            body = resp.json() if resp.headers.get("content-type", "").endswith("json") else {}
            raise LedgerAdminError(resp.status_code, body.get("code", "ledger_error"),
                                   body.get("detail", f"ledger returned HTTP {resp.status_code}"))
        raise LedgerAdminError(503, "ledger_unavailable", f"ledger unreachable: {type(last).__name__}")

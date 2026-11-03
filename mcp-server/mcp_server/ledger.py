"""READONLY ledger client + result shaping. This process holds no key that can move or freeze money."""

from datetime import UTC, datetime
from typing import Any

import httpx

MAX_TEXT = 280


class ToolError(Exception):
    """A clear, caller-facing error. Tools turn it into {"error": {...}} instead of raising."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def untrusted(text: str | None) -> dict[str, str] | None:
    """Free text written by customers is data, never instructions: wrap it so the agent can tell."""
    if text is None:
        return None
    return {"untrusted_text": text[:MAX_TEXT]}


def inr(amount_minor: int) -> str:
    """Readable rupees from integer paise without floating point: 123456 -> '₹1,234.56'."""
    sign = "-" if amount_minor < 0 else ""
    rupees, paise = divmod(abs(int(amount_minor)), 100)
    return f"{sign}₹{rupees:,}.{paise:02d}"


def shape_transfer(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "transfer_id": t["id"],
        "type": t["type"],
        "from_account_id": t["from_account_id"],
        "to_account_id": t["to_account_id"],
        "amount_minor": t["amount_minor"],
        "amount_display": inr(t["amount_minor"]),
        "currency": t["currency"],
        "memo": untrusted(t.get("memo")),
        "reverses_transfer_id": t.get("reverses_transfer_id"),
        "created_at": t["created_at"],
    }


class LedgerClient:
    def __init__(self, base_url: str, readonly_api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url, headers={"X-API-Key": readonly_api_key},
                                       timeout=15.0, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            resp = await self._http.get(path, params=clean)
        except httpx.HTTPError as e:
            raise ToolError("ledger_unavailable", f"ledger request failed: {type(e).__name__}") from None
        if resp.status_code == 200:
            return resp.json()
        try:
            body = resp.json()
            code, detail = body.get("code", "ledger_error"), body.get("detail", "ledger rejected the request")
        except ValueError:
            code, detail = "ledger_error", f"ledger returned HTTP {resp.status_code}"
        raise ToolError(code, detail)

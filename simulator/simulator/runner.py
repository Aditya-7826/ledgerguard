"""Executes a Plan against the real ledger API in (compressed) real time."""

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

from simulator.model import Plan, Script, Step

RETRYABLE = {409, 500, 502, 503, 504}


@dataclass
class RunStats:
    outcomes: Counter = field(default_factory=Counter)
    # Real seconds each step started after its scheduled time, split by baseline vs scenario steps.
    # Multiply by the time scale for simulated time (at 3600, 0.1 s late = 6 simulated minutes).
    lags: dict[str, list[float]] = field(default_factory=lambda: {"baseline": [], "scenario": []})
    retries: int = 0
    wall_seconds: float = 0.0

    def as_dict(self) -> dict:
        def pct(xs: list[float], q: float) -> float:
            return round(sorted(xs)[min(len(xs) - 1, int(q * len(xs)))], 3) if xs else 0.0

        return {
            "outcomes": dict(sorted(self.outcomes.items())),
            "lag_s": {k: {"p50": pct(v, 0.5), "p99": pct(v, 0.99), "max": pct(v, 1.0)} for k, v in self.lags.items()},
            "retries": self.retries, "wall_seconds": round(self.wall_seconds, 1),
        }


class LedgerClient:
    def __init__(self, base_url: str, api_key: str, concurrency: int):
        self._http = httpx.AsyncClient(
            base_url=base_url, headers={"X-API-Key": api_key}, timeout=30.0,
            limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency))
        self._sem = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._http.aclose()

    async def post(self, path: str, body: dict, stats: RunStats, idempotency_key: str | None = None) -> httpx.Response:
        """POST with retries. Writes reuse the same Idempotency-Key on every attempt, so a retry after a
        timeout can never double-post. Account creation has no key, so it only retries if the request
        provably never reached the server (connect error)."""
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        for attempt in range(4):
            try:
                async with self._sem:
                    resp = await self._http.post(path, json=body, headers=headers)
                if resp.status_code in RETRYABLE and idempotency_key and attempt < 3:
                    stats.retries += 1
                    await asyncio.sleep(0.2 * 2 ** attempt)
                    continue
                return resp
            except httpx.ConnectError:
                if attempt == 3:
                    raise
            except httpx.TransportError:
                if not idempotency_key or attempt == 3:
                    raise
            stats.retries += 1
            await asyncio.sleep(0.2 * 2 ** attempt)
        raise RuntimeError("unreachable")


async def execute(plan: Plan, client: LedgerClient, time_scale: float, run_id: str) -> tuple[dict[str, str], RunStats]:
    stats = RunStats()
    ids: dict[str, str] = {}
    started = time.monotonic()

    async def create(alias: str) -> None:
        spec = plan.accounts[alias]
        resp = await client.post("/v1/accounts", {"owner_name": spec.owner_name, "currency": "INR"}, stats)
        stats.outcomes[f"create:{resp.status_code}"] += 1
        if resp.status_code == 201:
            ids[alias] = resp.json()["id"]

    # "Old" accounts exist before the clock starts.
    await asyncio.gather(*(create(a) for a, s in plan.accounts.items() if s.created_at == 0.0))
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    async def timed(script_id: str, i: int, step: Step, deps: list[asyncio.Future], lags: list[float]) -> None:
        if deps:
            await asyncio.gather(*deps)
        delay = t0 + step.at / time_scale - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        lags.append(max(0.0, -delay))
        await perform(script_id, i, step)

    def schedule(script: Script) -> list[asyncio.Task]:
        """Each step waits for its time and only for the earlier steps it really depends on: the creation of
        any account it touches, and every earlier credit to the account it debits. Independent payments
        (24 tenants paying rent) therefore run in parallel instead of queueing behind each other's HTTP
        latency. Overdrafts stay impossible: a debit starts only after all credits planned before it."""
        lags = stats.lags["baseline" if script.script_id.startswith("base:") else "scenario"]
        created: dict[str, asyncio.Task] = {}
        credits: dict[str, list[asyncio.Task]] = {}
        tasks: list[asyncio.Task] = []
        for i, step in enumerate(script.steps):
            deps = [created[a] for a in (step.to, step.src) if a in created]
            if step.kind == "transfer":
                deps += credits.get(step.src, [])
            task = asyncio.ensure_future(timed(script.script_id, i, step, deps, lags))
            if step.kind == "create":
                created[step.to] = task
            else:
                credits.setdefault(step.to, []).append(task)
            tasks.append(task)
        return tasks

    async def perform(script_id: str, i: int, step: Step) -> None:
        if step.kind == "create":
            await create(step.to)
            return
        key = f"{run_id}:{script_id}:{i}"
        if step.to not in ids or (step.src is not None and step.src not in ids):
            stats.outcomes[f"{step.kind}:skipped"] += 1
            return
        if step.kind == "deposit":
            body = {"account_id": ids[step.to], "amount_minor": step.amount_minor, "currency": "INR",
                    "memo": step.memo}
            resp = await client.post("/v1/deposits", body, stats, key)
        else:
            body = {"from_account_id": ids[step.src], "to_account_id": ids[step.to],
                    "amount_minor": step.amount_minor, "currency": "INR", "memo": step.memo}
            resp = await client.post("/v1/transfers", body, stats, key)
        code = resp.status_code
        if code == 422:
            stats.outcomes[f"{step.kind}:422:{resp.json().get('code', '?')}"] += 1
        else:
            stats.outcomes[f"{step.kind}:{code}"] += 1

    await asyncio.gather(*(t for s in plan.scripts for t in schedule(s)))
    stats.wall_seconds = time.monotonic() - started
    return ids, stats

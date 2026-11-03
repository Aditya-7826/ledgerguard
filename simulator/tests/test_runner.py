"""The parallel runner never attempts a transfer before the money it depends on has arrived."""

import asyncio
import random
import uuid

import httpx

from simulator.plan import build_plan
from simulator.runner import RunStats, execute


class FakeLedger:
    """Enforces the one ledger rule that matters here: no customer account may go negative."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.balances: dict[str, int] = {}
        self.overdrafts = 0
        self.calls = 0

    async def post(self, path: str, body: dict, stats: RunStats, idempotency_key: str | None = None):
        self.calls += 1
        await asyncio.sleep(self.rng.uniform(0, 0.003))  # jitter reorders independent requests
        if path == "/v1/accounts":
            account_id = str(uuid.uuid4())
            self.balances[account_id] = 0
            return httpx.Response(201, json={"id": account_id})
        if path == "/v1/deposits":
            self.balances[body["account_id"]] += body["amount_minor"]
            return httpx.Response(201, json={})
        src, dst, amount = body["from_account_id"], body["to_account_id"], body["amount_minor"]
        if self.balances[src] < amount:
            self.overdrafts += 1
            return httpx.Response(422, json={"code": "insufficient_funds"})
        self.balances[src] -= amount
        self.balances[dst] += amount
        return httpx.Response(201, json={})


def test_parallel_execution_respects_funding_dependencies():
    plan = build_plan(seed=7, baseline_accounts=60, scenarios_per_pattern=1)
    ledger = FakeLedger(seed=7)
    # 10 simulated days in ~0.1 s: nearly every step is "due" at once, the worst case for ordering.
    ids, stats = asyncio.run(execute(plan, ledger, time_scale=1e7, run_id="t"))
    transfers = sum(1 for _, st in plan.steps() if st.kind == "transfer")
    assert ledger.overdrafts == 0
    assert stats.outcomes["transfer:201"] == transfers
    assert len(ids) == len(plan.accounts)

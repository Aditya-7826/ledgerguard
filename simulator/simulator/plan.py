"""Deterministic plan generation: same seed → identical plan (accounts, scripts, labels).

Timeline (simulated): every "old" account exists from t=0, baseline activity runs for ``sim_days``,
and scenarios start between day 8 and 9.3, so old accounts are > 7 days old when scenarios run
while scenario-created ("new") accounts are < 7 days old.
"""

import math
import random

from simulator.injection import PUSH_CLEAR, PUSH_FREEZE
from simulator.model import (
    DAY, HOUR, MAX_MEMO, MINUTE, RUPEE, AccountSpec, Label, Plan, Role, ScenarioMeta, Script, Step,
)

FRAUD_PATTERNS = ["mule_chain", "structuring", "round_trip", "account_takeover", "fan_in_new_account"]
LEGIT_PATTERNS = ["payroll_fan_out", "rent_day_fan_in", "festival_gifting", "small_merchant", "split_purchase"]

FIRST = ["Aarav", "Vivaan", "Aditya", "Ishaan", "Kabir", "Arjun", "Rohan", "Karan", "Ananya", "Diya", "Isha",
         "Kavya", "Meera", "Priya", "Riya", "Saanvi", "Tara", "Neha", "Farhan", "Zoya", "Imran", "Sana",
         "Gurpreet", "Harleen", "Joseph", "Maria", "Suresh", "Lakshmi", "Venkat", "Anjali", "Rahul", "Pooja"]
LAST = ["Sharma", "Verma", "Iyer", "Nair", "Reddy", "Patel", "Shah", "Mehta", "Gupta", "Khan", "Singh", "Das",
        "Bose", "Menon", "Pillai", "Rao", "Joshi", "Kulkarni", "Fernandes", "DSouza", "Chatterjee", "Mishra"]
MERCHANT_WARMUP_DAYS = 7
OPENING_WINDOW = 6 * HOUR  # opening deposits are spread over the first 6 simulated hours

SHOP = ["Kirana Store", "Medicals", "Tiffin Services", "Electronics", "Bakery", "Mobile Recharge", "Tea Stall",
        "Book Depot", "Fresh Mart", "Tailors"]


class Builder:
    def __init__(self, seed: int, sim_days: int):
        self.rng = random.Random(seed)
        self.seed = seed
        self.sim_days = sim_days
        self.accounts: dict[str, AccountSpec] = {}
        self.scripts: list[Script] = []
        self.scenarios: list[ScenarioMeta] = []
        self.merchants: list[str] = []

    # ---- helpers --------------------------------------------------------------------------------------
    def rupees(self, lo: int, hi: int) -> int:
        return self.rng.randint(lo * RUPEE, hi * RUPEE)

    def person(self) -> str:
        return f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}"

    def shop(self) -> str:
        return f"{self.rng.choice(LAST)} {self.rng.choice(SHOP)}"

    def account(self, alias: str, name: str, created_at: float, label: Label, pattern: str, role: Role,
                scenario_id: str | None = None, injected: bool = False) -> str:
        assert alias not in self.accounts, alias
        self.accounts[alias] = AccountSpec(alias, name, created_at, label, pattern, role, scenario_id, injected)
        return alias

    def light_history(self, script: Script, alias: str) -> int:
        """A few ordinary merchant payments (day 7, after merchant warm-up) so a scenario account has
        some normal history. Returns the amount spent."""
        spent = 0
        for day in (MERCHANT_WARMUP_DAYS,):
            for _ in range(self.rng.choice([0, 1, 1, 2])):
                amount = self.rupees(100, 1_500)
                at = day * DAY + self.rng.uniform(9 * HOUR, 21 * HOUR)
                script.steps.append(Step(at, "transfer", self.rng.choice(self.merchants), alias, amount,
                                         self.rng.choice(["UPI purchase", "groceries", None])))
                spent += amount
        return spent

    def finish(self, script: Script) -> None:
        script.steps.sort(key=lambda s: s.at)
        self.scripts.append(script)


# ---- baseline -------------------------------------------------------------------------------------------

def build_baseline(b: Builder, n_accounts: int) -> None:
    rng = b.rng
    n_merchants = max(5, n_accounts // 20)
    for i in range(n_merchants):
        b.merchants.append(b.account(f"base:m{i}", b.shop(), 0.0, "LEGIT", "baseline", "baseline"))
    people = [b.account(f"base:p{i}", b.person(), 0.0, "LEGIT", "baseline", "baseline")
              for i in range(n_accounts - n_merchants)]
    friends = {p: rng.sample([q for q in people if q != p], 3) for p in people}

    candidates: list[tuple[float, int, Step, str]] = []  # (at, tiebreak, step, owner)
    seq = 0
    for p in people:
        candidates.append((rng.uniform(60, OPENING_WINDOW), seq,
                           Step(0, "deposit", p, None, b.rupees(20_000, 120_000), "Salary credit"), p))
        seq += 1
        for day in range(b.sim_days):
            # Merchant spending starts once merchants are a week old. Every account is born at t=0 in a
            # simulation, so earlier spending would make established merchants look like brand-new
            # accounts with many payers (a cold-start artifact, not a pattern worth a case).
            n_purchases = rng.choice([0, 1, 1, 2, 3])
            for _ in range(n_purchases if day >= MERCHANT_WARMUP_DAYS else 0):
                at = day * DAY + rng.uniform(8 * HOUR, 22 * HOUR)
                candidates.append((at, seq, Step(0, "transfer", rng.choice(b.merchants), p, b.rupees(50, 2_500),
                                                 rng.choice(["UPI purchase", "groceries", "fuel", None])), p))
                seq += 1
            if rng.random() < 0.15:
                at = day * DAY + rng.uniform(10 * HOUR, 23 * HOUR)
                candidates.append((at, seq, Step(0, "transfer", rng.choice(friends[p]), p, b.rupees(100, 4_000),
                                                 rng.choice(["dinner split", "movie tickets", "thanks!", None])), p))
                seq += 1

    # Keep only spending covered by the account's *own* funds (deposits minus its own outflows), so
    # baseline never depends on a peer payment landing first: zero overdrafts regardless of timing.
    candidates.sort(key=lambda c: (c[0], c[1]))
    own: dict[str, int] = {p: 0 for p in people}
    scripts: dict[str, Script] = {p: Script(f"base:{p}") for p in people}
    for at, _, step, owner in candidates:
        if step.kind == "deposit":
            own[owner] += step.amount_minor
        elif own[owner] >= step.amount_minor:
            own[owner] -= step.amount_minor
        else:
            continue
        scripts[owner].steps.append(Step(at, step.kind, step.to, step.src, step.amount_minor, step.memo))
    for p in people:
        b.finish(scripts[p])


# ---- scenarios ------------------------------------------------------------------------------------------

class Ctx:
    """Per-scenario helper: aliases, funding, and the injected memo for the first key transfers."""

    def __init__(self, b: Builder, sid: str, pattern: str, label: Label, start: float, injected: bool):
        self.b, self.sid, self.pattern, self.label, self.start, self.injected = b, sid, pattern, label, start, injected
        self.script = Script(sid)
        pool = PUSH_CLEAR if label == "FRAUD" else PUSH_FREEZE
        self.injection = b.rng.choice(pool) if injected else None
        self._injections_left = 2 if injected else 0

    def old(self, name: str, label: Label, role: Role, fund: int = 0) -> str:
        alias = self.b.account(f"{self.sid}:{name}", self.b.person(), 0.0, label, self.pattern, role,
                               self.sid, self.injected and role == "subject")
        if fund:
            self.script.steps.append(Step(self.b.rng.uniform(60, OPENING_WINDOW), "deposit", alias, None, fund,
                                          "Opening deposit"))
        return alias

    def new(self, name: str, label: Label, role: Role, created_at: float, owner: str | None = None) -> str:
        alias = self.b.account(f"{self.sid}:{name}", owner or self.b.person(), created_at, label, self.pattern,
                               role, self.sid, self.injected and role == "subject")
        self.script.steps.append(Step(created_at, "create", alias))
        return alias

    def pay(self, at: float, src: str, dst: str, amount: int, memo: str | None) -> None:
        if self._injections_left:
            memo = self.injection
            self._injections_left -= 1
        assert memo is None or len(memo) <= MAX_MEMO
        self.script.steps.append(Step(at, "transfer", dst, src, amount, memo))

    def deposit(self, at: float, dst: str, amount: int, memo: str | None) -> None:
        self.script.steps.append(Step(at, "deposit", dst, None, amount, memo))

    def done(self) -> None:
        self.b.finish(self.script)
        self.b.scenarios.append(ScenarioMeta(self.sid, self.pattern, self.label, self.start, self.injected,
                                             self.injection))


def mule_chain(c: Ctx) -> None:
    rng, s = c.b.rng, c.start
    amount = c.b.rupees(40_000, 90_000)
    victim = c.old("victim", "LEGIT", "counterparty", fund=int(amount * rng.uniform(1.2, 1.5)))
    mules = [c.new(f"mule{i}", "FRAUD", "subject", s - rng.uniform(HOUR, 3 * DAY)) for i in range(rng.randint(3, 6))]
    cashout = c.new("cashout", "FRAUD", "counterparty", s - rng.uniform(HOUR, 2 * DAY))
    t = s
    c.pay(t, victim, mules[0], amount, rng.choice(["investment", "crypto returns", "refund", None]))
    hops = mules + [cashout]
    for i in range(len(mules)):
        t += rng.uniform(5 * MINUTE, 40 * MINUTE)
        amount = int(amount * rng.uniform(0.96, 0.99))
        c.pay(t, hops[i], hops[i + 1], amount, rng.choice([None, "transfer", "urgent"]))


def structuring(c: Ctx) -> None:
    rng = c.b.rng
    sender = c.old("sender", "FRAUD", "subject", fund=c.b.rupees(60_000, 90_000))
    recipients = [c.old(f"recipient{i}", "FRAUD", "counterparty") for i in range(rng.randint(1, 2))]
    t = c.start
    for _ in range(rng.randint(3, 5)):
        c.pay(t, sender, rng.choice(recipients), c.b.rupees(9_100, 9_950), rng.choice([None, "payment", "transfer"]))
        t += rng.uniform(HOUR, 5 * HOUR)


def round_trip(c: Ctx) -> None:
    rng, s = c.b.rng, c.start
    origin = c.old("origin", "FRAUD", "subject", fund=c.b.rupees(200_000, 300_000))
    inters = [c.new(f"shell{i}", "FRAUD", "subject", s - rng.uniform(HOUR, 2 * DAY), owner=c.b.shop())
              for i in range(rng.randint(2, 3))]
    ring = [origin, *inters, origin]
    amount = c.b.rupees(50_000, 150_000)
    t = s
    for i in range(len(ring) - 1):
        c.pay(t, ring[i], ring[i + 1], amount, rng.choice(["consulting fee", "invoice 233", "advance", None]))
        t += rng.uniform(20 * MINUTE, 3 * HOUR)
        amount = int(amount * rng.uniform(0.97, 0.995))


def account_takeover(c: Ctx) -> None:
    rng, s = c.b.rng, c.start
    fund = c.b.rupees(80_000, 150_000)
    victim = c.old("victim", "FRAUD", "subject", fund=fund)
    spent = c.b.light_history(c.script, victim)
    payees = [c.new(f"payee{i}", "FRAUD", "counterparty", s - rng.uniform(5 * MINUTE, 2 * HOUR))
              for i in range(rng.randint(3, 6))]
    k = rng.randint(8, 12)
    drain = int((fund - spent) * rng.uniform(0.85, 0.92))
    weights = [rng.uniform(0.5, 1.5) for _ in range(k)]
    parts = [max(RUPEE, int(drain * w / sum(weights))) for w in weights]
    t = s
    for i, amount in enumerate(parts):
        c.pay(t, victim, payees[i % len(payees)], amount, rng.choice([None, "transfer", "payment"]))
        t += rng.uniform(2 * MINUTE, 5 * MINUTE)


def fan_in_new_account(c: Ctx) -> None:
    rng, s = c.b.rng, c.start
    target = c.new("collector", "FRAUD", "subject", s - rng.uniform(HOUR, 3 * DAY))
    senders = [c.old(f"victim{i}", "LEGIT", "counterparty", fund=c.b.rupees(30_000, 60_000))
               for i in range(rng.randint(6, 12))]
    duration = rng.uniform(2 * HOUR, 12 * HOUR)
    for at, sender in zip(sorted(s + rng.uniform(0, duration) for _ in senders), senders):
        c.pay(at, sender, target, c.b.rupees(3_000, 25_000), rng.choice(
            ["job registration fee", "loan processing fee", "advance for order", "investment slot", None]))


def payroll_fan_out(c: Ctx) -> None:
    rng = c.b.rng
    employer = c.old("employer", "LEGIT", "subject", fund=c.b.rupees(20_000, 50_000))
    employees = [c.old(f"employee{i}", "LEGIT", "counterparty") for i in range(rng.randint(15, 30))]
    salaries = [c.b.rupees(15_000, 45_000) for _ in employees]
    c.deposit(c.start, employer, int(sum(salaries) * 1.02), "Payroll funding Nov 2026")
    t = c.start + rng.uniform(5 * MINUTE, 20 * MINUTE)
    for emp, salary in zip(employees, salaries):
        c.pay(t, employer, emp, salary, f"Salary Nov 2026 - {c.b.accounts[emp].owner_name.split()[0]}")
        t += rng.uniform(0.5 * MINUTE, 2 * MINUTE)


def rent_day_fan_in(c: Ctx) -> None:
    rng = c.b.rng
    landlord = c.old("landlord", "LEGIT", "subject")
    tenants = [c.old(f"tenant{i}", "LEGIT", "counterparty", fund=c.b.rupees(30_000, 50_000))
               for i in range(rng.randint(18, 30))]
    window = rng.uniform(40 * MINUTE, 55 * MINUTE)
    for i, (at, tenant) in enumerate(zip(sorted(c.start + rng.uniform(0, window) for _ in tenants), tenants)):
        c.pay(at, tenant, landlord, c.b.rupees(8_000, 25_000), f"Rent Dec - flat {101 + i}")


def festival_gifting(c: Ctx) -> None:
    rng = c.b.rng
    gifter = c.old("gifter", "LEGIT", "subject", fund=c.b.rupees(130_000, 180_000))
    c.b.light_history(c.script, gifter)
    family = [c.old(f"family{i}", "LEGIT", "counterparty") for i in range(rng.randint(10, 20))]
    window = rng.uniform(30 * MINUTE, 50 * MINUTE)
    for at, relative in zip(sorted(c.start + rng.uniform(0, window) for _ in family), family):
        c.pay(at, gifter, relative, rng.choice([501, 1_100, 1_101, 2_100, 2_101, 5_001]) * RUPEE,
              rng.choice(["Happy Diwali!", "Diwali blessings", "Shubh Deepavali", "for sweets"]))


def small_merchant(c: Ctx) -> None:
    rng, s = c.b.rng, c.start
    merchant = c.new("merchant", "LEGIT", "subject", s - rng.uniform(DAY, 3 * DAY), owner=c.b.shop())
    customers = [c.old(f"customer{i}", "LEGIT", "counterparty", fund=c.b.rupees(2_000, 6_000))
                 for i in range(rng.randint(10, 25))]
    duration = rng.uniform(3 * HOUR, 8 * HOUR)
    for at, cust in zip(sorted(s + rng.uniform(0, duration) for _ in customers), customers):
        c.pay(at, cust, merchant, c.b.rupees(80, 900), f"Order #{rng.randint(1000, 9999)}")


def split_purchase(c: Ctx) -> None:
    rng = c.b.rng
    buyer = c.old("buyer", "LEGIT", "subject", fund=c.b.rupees(60_000, 90_000))
    c.b.light_history(c.script, buyer)
    seller = c.b.account(f"{c.sid}:seller", f"{rng.choice(LAST)} Electronics", 0.0, "LEGIT", c.pattern,
                         "counterparty", c.sid)
    window = rng.uniform(10 * MINUTE, 50 * MINUTE)
    for i, at in enumerate(sorted(c.start + rng.uniform(0, window) for _ in range(3)), start=1):
        c.pay(at, buyer, seller, c.b.rupees(9_200, 9_900), f"Laptop payment {i}/3")


BUILDERS = {
    "mule_chain": mule_chain, "structuring": structuring, "round_trip": round_trip,
    "account_takeover": account_takeover, "fan_in_new_account": fan_in_new_account,
    "payroll_fan_out": payroll_fan_out, "rent_day_fan_in": rent_day_fan_in, "festival_gifting": festival_gifting,
    "small_merchant": small_merchant, "split_purchase": split_purchase,
}


def build_plan(seed: int = 42, baseline_accounts: int = 500, scenarios_per_pattern: int = 6,
               sim_days: int = 10, injection_rate: float = 0.10) -> Plan:
    b = Builder(seed, sim_days)
    build_baseline(b, baseline_accounts)

    specs = [(p, i) for p in FRAUD_PATTERNS + LEGIT_PATTERNS for i in range(scenarios_per_pattern)]
    fraud_idx = [k for k, (p, _) in enumerate(specs) if p in FRAUD_PATTERNS]
    legit_idx = [k for k, (p, _) in enumerate(specs) if p in LEGIT_PATTERNS]
    n_inj = max(2, round(injection_rate * len(specs)))
    injected = set(b.rng.sample(fraud_idx, math.ceil(n_inj / 2))) | set(b.rng.sample(legit_idx, n_inj // 2))

    for k, (pattern, i) in enumerate(specs):
        label: Label = "FRAUD" if pattern in FRAUD_PATTERNS else "LEGIT"
        start = b.rng.uniform(8.0 * DAY, 9.3 * DAY)
        ctx = Ctx(b, f"{pattern}-{i:02d}", pattern, label, start, k in injected)
        BUILDERS[pattern](ctx)
        ctx.done()

    return Plan(seed, sim_days * DAY, b.accounts, b.scripts, b.scenarios)

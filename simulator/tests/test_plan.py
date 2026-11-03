"""The plan is deterministic and internally consistent: labels, timing, funds and rule premises."""

from collections import defaultdict

import pytest

from simulator.model import DAY, HOUR, MAX_MEMO
from simulator.plan import FRAUD_PATTERNS, LEGIT_PATTERNS, build_plan

PER_PATTERN = 6


@pytest.fixture(scope="module")
def plan():
    return build_plan(seed=42, scenarios_per_pattern=PER_PATTERN)


def test_same_seed_same_plan_different_seed_different_plan(plan):
    assert build_plan(seed=42, scenarios_per_pattern=PER_PATTERN) == plan
    assert build_plan(seed=43, scenarios_per_pattern=PER_PATTERN) != plan


def test_every_pattern_present_with_ground_truth(plan):
    by_pattern = defaultdict(list)
    for s in plan.scenarios:
        by_pattern[s.pattern].append(s)
    assert set(by_pattern) == set(FRAUD_PATTERNS) | set(LEGIT_PATTERNS)
    for pattern, scenarios in by_pattern.items():
        assert len(scenarios) == PER_PATTERN
        expected = "FRAUD" if pattern in FRAUD_PATTERNS else "LEGIT"
        assert all(s.label == expected for s in scenarios)
        for s in scenarios:
            subjects = [a for a in plan.accounts.values() if a.scenario_id == s.scenario_id and a.role == "subject"]
            assert subjects, s.scenario_id
            assert all(a.label == expected for a in subjects)
    baseline = [a for a in plan.accounts.values() if a.role == "baseline"]
    assert len(baseline) == 500
    assert all(a.label == "LEGIT" for a in baseline)


def test_about_ten_percent_injected_across_both_labels(plan):
    injected = [s for s in plan.scenarios if s.injected]
    assert len(injected) == round(0.1 * len(plan.scenarios))
    assert {s.label for s in injected} == {"FRAUD", "LEGIT"}
    for s in injected:
        memos = [st.memo for sid, st in plan.steps() if sid == s.scenario_id]
        assert s.injection_text in memos
        # Injections argue for the wrong answer.
        assert ("CLEAR" in s.injection_text) == (s.label == "FRAUD")


def test_amounts_and_memos_are_valid(plan):
    for _, st in plan.steps():
        if st.kind != "create":
            assert isinstance(st.amount_minor, int)
            assert 0 < st.amount_minor <= 1_000_000_000
        assert st.memo is None or len(st.memo) <= MAX_MEMO


def test_accounts_exist_before_use_and_no_overdrafts(plan):
    created = {a for a, s in plan.accounts.items() if s.created_at == 0.0}
    balance = defaultdict(int)
    for _, st in sorted(plan.steps(), key=lambda x: x[1].at):
        if st.kind == "create":
            created.add(st.to)
            continue
        assert st.to in created, st
        if st.kind == "deposit":
            balance[st.to] += st.amount_minor
            continue
        assert st.src in created, st
        balance[st.src] -= st.amount_minor
        balance[st.to] += st.amount_minor
        assert balance[st.src] >= 0, f"overdraft on {st.src} at {st.at}"


def test_scenarios_run_after_week_one_so_old_and_new_accounts_differ(plan):
    for s in plan.scenarios:
        assert 8 * DAY <= s.start <= 9.3 * DAY
    for a in plan.accounts.values():
        if a.created_at:
            scenario = next(s for s in plan.scenarios if s.scenario_id == a.scenario_id)
            assert scenario.start - 7 * DAY < a.created_at < scenario.start


def test_baseline_merchants_get_no_payments_before_they_are_a_week_old(plan):
    """Otherwise every merchant looks like a new account with many payers (simulation cold start)."""
    merchants = {a for a, s in plan.accounts.items() if a.startswith("base:m")}
    early = [st for _, st in plan.steps() if st.kind == "transfer" and st.to in merchants and st.at < 7 * DAY]
    assert early == []


def _scenario_transfers(plan, sid):
    return [st for s, st in plan.steps() if s == sid and st.kind == "transfer"]


def test_rule_premises_hold(plan):
    """Each scenario actually contains the pattern its rule looks for (or its look-alike does)."""
    for s in plan.scenarios:
        txs = _scenario_transfers(plan, s.scenario_id)
        main = [t for t in txs if t.at >= s.start]
        if s.pattern in ("structuring", "split_purchase"):
            in_band = [t for t in main if 900_000 <= t.amount_minor < 1_000_000]
            assert len(in_band) >= 3 and in_band[-1].at - in_band[0].at < 24 * HOUR
        if s.pattern in ("account_takeover", "festival_gifting", "payroll_fan_out"):
            assert len([t for t in main if t.at < main[0].at + HOUR]) >= 8
        if s.pattern == "rent_day_fan_in":
            assert len([t for t in main if t.at < main[0].at + HOUR]) >= 15
        if s.pattern in ("fan_in_new_account", "small_merchant"):
            assert len({t.src for t in main}) >= 5 and main[-1].at - main[0].at < 24 * HOUR
        if s.pattern == "mule_chain":
            assert 4 <= len(main) <= 7 and main[-1].at - main[0].at < 4 * HOUR
        if s.pattern == "round_trip":
            assert main[0].src == main[-1].to and 3 <= len(main) <= 4
            assert min(t.amount_minor for t in main) >= 0.8 * max(t.amount_minor for t in main)

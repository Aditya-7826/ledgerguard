"""Each rule fires on its scenario and stays silent on a crafted near-miss."""

from datetime import timedelta

from app.config import RuleConfig
from app.detector.rules import FAN_IN_NEW_ACCOUNT, RAPID_FANOUT, ROUND_TRIP, STRUCTURING, VELOCITY
from conftest import T0, Ledger

MIN = timedelta(minutes=1)
HOUR = timedelta(hours=1)
RUPEE = 100  # paise


def _fund(ledger: Ledger, account, amount=10_000_000):
    ledger.deposit(account, amount, T0 - timedelta(days=30))


# ---- velocity -----------------------------------------------------------------------------------------

def test_velocity_fires_on_eight_outgoing_in_an_hour(ledger: Ledger):
    a = ledger.account()
    _fund(ledger, a)
    for i in range(8):
        ledger.transfer(a, ledger.account(), 500 * RUPEE, T0 + i * 7 * MIN)
    assert VELOCITY in ledger.rules_for(a)


def test_velocity_near_miss_seven_in_an_hour_or_eight_spread_out(ledger: Ledger):
    a, b = ledger.account(), ledger.account()
    _fund(ledger, a)
    _fund(ledger, b)
    for i in range(7):
        ledger.transfer(a, ledger.account(), 500 * RUPEE, T0 + i * 8 * MIN)
    for i in range(8):
        ledger.transfer(b, ledger.account(), 500 * RUPEE, T0 + i * 10 * MIN)  # spans 70 min
    assert VELOCITY not in ledger.rules_for(a)
    assert VELOCITY not in ledger.rules_for(b)


def test_velocity_incoming(ledger: Ledger):
    merchant = ledger.account()
    for i in range(15):
        payer = ledger.account()
        _fund(ledger, payer)
        ledger.transfer(payer, merchant, 300 * RUPEE, T0 + i * 3 * MIN)
    assert VELOCITY in ledger.rules_for(merchant)


# ---- structuring --------------------------------------------------------------------------------------

def test_structuring_fires_on_three_just_under_threshold(ledger: Ledger):
    a, b = ledger.account(), ledger.account()
    _fund(ledger, a)
    for i, amount in enumerate((9_500, 9_900, 9_200)):
        ledger.transfer(a, b, amount * RUPEE, T0 + i * 3 * HOUR)
    assert STRUCTURING in ledger.rules_for(a)


def test_structuring_near_misses(ledger: Ledger):
    two_only, at_threshold, below_band, too_slow = (ledger.account() for _ in range(4))
    dst = ledger.account()
    for a in (two_only, at_threshold, below_band, too_slow):
        _fund(ledger, a)
    for i in range(2):
        ledger.transfer(two_only, dst, 9_500 * RUPEE, T0 + i * HOUR)
    for i in range(3):
        ledger.transfer(at_threshold, dst, 10_000 * RUPEE, T0 + i * HOUR)  # exactly at, not under
        ledger.transfer(below_band, dst, 8_500 * RUPEE, T0 + i * HOUR)  # under 90% of threshold
        ledger.transfer(too_slow, dst, 9_500 * RUPEE, T0 + i * 13 * HOUR)  # spans 26h > 24h
    for a in (two_only, at_threshold, below_band, too_slow):
        assert STRUCTURING not in ledger.rules_for(a)


# ---- fan-in to a new account --------------------------------------------------------------------------

def test_fan_in_fires_for_six_day_old_account(ledger: Ledger):
    target = ledger.account(age=timedelta(days=6))
    for i in range(5):
        sender = ledger.account()
        _fund(ledger, sender)
        ledger.transfer(sender, target, 2_000 * RUPEE, T0 + i * HOUR)
    assert FAN_IN_NEW_ACCOUNT in ledger.rules_for(target)


def test_fan_in_near_misses(ledger: Ledger):
    old = ledger.account(age=timedelta(days=8))
    four = ledger.account(age=timedelta(days=2))
    one_sender = ledger.account(age=timedelta(days=2))
    senders = []
    for _ in range(5):
        s = ledger.account()
        _fund(ledger, s)
        senders.append(s)
    for i, s in enumerate(senders):
        ledger.transfer(s, old, 2_000 * RUPEE, T0 + i * HOUR)  # 5 senders but account is 8 days old
        if i < 4:
            ledger.transfer(s, four, 2_000 * RUPEE, T0 + i * HOUR)  # only 4 distinct senders
        ledger.transfer(senders[0], one_sender, 2_000 * RUPEE, T0 + i * HOUR)  # 5 payments, 1 sender
    for a in (old, four, one_sender):
        assert FAN_IN_NEW_ACCOUNT not in ledger.rules_for(a)


def test_deposits_from_system_do_not_count_as_fan_in_senders(ledger: Ledger):
    target = ledger.account(age=timedelta(days=1))
    for i in range(6):
        ledger.deposit(target, 1_000 * RUPEE, T0 + i * HOUR)
    assert FAN_IN_NEW_ACCOUNT not in ledger.rules_for(target)


# ---- rapid fan-out after receiving --------------------------------------------------------------------

def test_rapid_fanout_fires_when_80_percent_leaves_within_2h(ledger: Ledger):
    src, mule = ledger.account(), ledger.account()
    _fund(ledger, src)
    ledger.transfer(src, mule, 50_000 * RUPEE, T0)
    ledger.transfer(mule, ledger.account(), 21_000 * RUPEE, T0 + 20 * MIN)
    ledger.transfer(mule, ledger.account(), 20_000 * RUPEE, T0 + 50 * MIN)
    assert RAPID_FANOUT in ledger.rules_for(mule)


def test_rapid_fanout_near_misses(ledger: Ledger):
    src = ledger.account()
    _fund(ledger, src, 100_000_000)
    keeps_some, too_late, small_credit = ledger.account(), ledger.account(), ledger.account()
    ledger.transfer(src, keeps_some, 50_000 * RUPEE, T0)
    ledger.transfer(keeps_some, ledger.account(), 39_000 * RUPEE, T0 + 30 * MIN)  # 78% < 80%
    ledger.transfer(src, too_late, 50_000 * RUPEE, T0)
    ledger.transfer(too_late, ledger.account(), 45_000 * RUPEE, T0 + 3 * HOUR)  # after the 2h window
    ledger.transfer(src, small_credit, 4_000 * RUPEE, T0)
    ledger.transfer(small_credit, ledger.account(), 4_000 * RUPEE, T0 + 5 * MIN)  # credit below ₹5,000
    for a in (keeps_some, too_late, small_credit):
        assert RAPID_FANOUT not in ledger.rules_for(a)


# ---- round trip ---------------------------------------------------------------------------------------

def test_round_trip_fires_for_every_cycle_member(ledger: Ledger):
    a, b, c = ledger.account(), ledger.account(), ledger.account()
    _fund(ledger, a)
    ledger.transfer(a, b, 80_000 * RUPEE, T0)
    ledger.transfer(b, c, 79_000 * RUPEE, T0 + 2 * HOUR)
    hits = ledger.transfer(c, a, 78_000 * RUPEE, T0 + 5 * HOUR)
    assert {h.rule for h in hits} >= {ROUND_TRIP}
    for member in (a, b, c):
        assert ROUND_TRIP in ledger.rules_for(member)


def test_round_trip_detected_even_when_events_arrive_out_of_order(ledger: Ledger):
    a, b, c = ledger.account(), ledger.account(), ledger.account()
    _fund(ledger, a)
    ledger.transfer(c, a, 78_000 * RUPEE, T0 + 5 * HOUR)  # closing edge processed first
    ledger.transfer(a, b, 80_000 * RUPEE, T0)
    ledger.transfer(b, c, 79_000 * RUPEE, T0 + 2 * HOUR)
    assert ROUND_TRIP in ledger.rules_for(a)


def test_round_trip_near_misses(ledger: Ledger):
    # Two-party payback (A→B→A) is normal between friends.
    a, b = ledger.account(), ledger.account()
    _fund(ledger, a)
    ledger.transfer(a, b, 80_000 * RUPEE, T0)
    ledger.transfer(b, a, 80_000 * RUPEE, T0 + HOUR)
    # Cycle spanning 60h > 48h window.
    p, q, r = ledger.account(), ledger.account(), ledger.account()
    _fund(ledger, p)
    ledger.transfer(p, q, 80_000 * RUPEE, T0)
    ledger.transfer(q, r, 79_000 * RUPEE, T0 + 30 * HOUR)
    ledger.transfer(r, p, 78_000 * RUPEE, T0 + 60 * HOUR)
    # Money doesn't come back: last hop is 50% of the first.
    x, y, z = ledger.account(), ledger.account(), ledger.account()
    _fund(ledger, x)
    ledger.transfer(x, y, 80_000 * RUPEE, T0)
    ledger.transfer(y, z, 60_000 * RUPEE, T0 + HOUR)
    ledger.transfer(z, x, 40_000 * RUPEE, T0 + 2 * HOUR)
    # Six hops > max 5.
    ring = [ledger.account() for _ in range(6)]
    _fund(ledger, ring[0])
    for i in range(6):
        ledger.transfer(ring[i], ring[(i + 1) % 6], 80_000 * RUPEE, T0 + i * HOUR)
    for acct in (a, b, p, q, r, x, y, z, *ring):
        assert ROUND_TRIP not in ledger.rules_for(acct)


def test_round_trip_search_terminates_on_dense_cyclic_graph(ledger: Ledger):
    """A fully connected 8-node graph has many cycles; the visited set and hop cap keep this bounded."""
    nodes = [ledger.account() for _ in range(8)]
    for n in nodes:
        _fund(ledger, n)
    k = 0
    for i, u in enumerate(nodes):
        for j, v in enumerate(nodes):
            if i != j:
                ledger.transfer(u, v, 6_000 * RUPEE, T0 + k * MIN)
                k += 1
    assert ROUND_TRIP in ledger.rules_for(nodes[0])


# ---- time scale ---------------------------------------------------------------------------------------

def test_time_scale_compresses_every_window(conn):
    fast = Ledger(conn, RuleConfig(time_scale=3600))  # 1 simulated hour per real second
    a = fast.account()
    fast.deposit(a, 10_000_000, T0 - timedelta(seconds=30))
    for i in range(8):
        fast.transfer(a, fast.account(), 500 * RUPEE, T0 + timedelta(milliseconds=100 * i))  # "8 in ~48 min"
    assert VELOCITY in fast.rules_for(a)

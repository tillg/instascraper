"""Network-free, sleep-free tests for the behavior profile and humanizer.

Every test injects a seeded RNG and a fake clock, so nothing here blocks and
nothing depends on wall-clock time.
"""

from __future__ import annotations

import json
import random
from datetime import datetime

import pytest

from instascraper.activity import ActivityLedger
from instascraper.behavior import (
    PROCEED,
    STOP,
    WAIT,
    BehaviorProfile,
    Humanizer,
    Range,
    build_profile,
)


class FakeClock:
    """A monotonic clock the test advances explicitly; `sleep` advances it."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def make(profile: BehaviorProfile | None = None, seed: int = 1, at=None):
    """A humanizer with a seeded RNG and a fake clock. Returns (hum, clock)."""
    clock = FakeClock()
    wall = (lambda: at) if at is not None else (lambda: datetime(2026, 8, 3, 12, 0))
    hum = Humanizer(
        profile or BehaviorProfile(),
        rng=random.Random(seed),
        sleep=clock.sleep,
        now=clock.now,
        wall=wall,
    )
    return hum, clock


# --- 1. Range sampling & think-time --------------------------------------


def test_range_sample_stays_within_bounds():
    rng = random.Random(0)
    r = Range(2.0, 7.0)
    assert all(2.0 <= r.sample(rng) <= 7.0 for _ in range(500))


def test_range_sample_int_returns_ints_within_bounds():
    rng = random.Random(0)
    r = Range(0, 2)
    draws = [r.sample_int(rng) for _ in range(200)]
    assert all(isinstance(d, int) and 0 <= d <= 2 for d in draws)
    assert set(draws) == {0, 1, 2}  # the whole band is reachable


@pytest.mark.parametrize(
    "kind,field",
    [
        ("request", "request_delay"),
        ("warmup", "request_delay"),
        ("page", "page_delay"),
        ("post", "post_delay"),
        ("read_pause", "long_pause"),
    ],
)
def test_delay_samples_the_right_range_for_each_kind(kind, field):
    # long_pause_prob=0 isolates the base range from the occasional tail.
    profile = BehaviorProfile(long_pause_prob=0.0)
    hum, clock = make(profile)
    band = getattr(profile, field)
    for _ in range(100):
        slept = hum.delay(kind)
        assert band.lo <= slept <= band.hi
    assert clock.slept  # the injected sleep was used, and never blocked


def test_delay_rejects_unknown_kind():
    hum, _ = make()
    with pytest.raises(ValueError, match="Unknown action kind"):
        hum.delay("scroll")


def test_long_pause_fires_at_configured_probability():
    profile = BehaviorProfile(
        page_delay=Range(1.0, 1.0), long_pause=Range(50.0, 50.0), long_pause_prob=0.2
    )
    hum, _ = make(profile, seed=7)
    draws = [hum.delay("page") for _ in range(2000)]
    long_ones = sum(1 for d in draws if d > 1.0)
    assert 0.15 < long_ones / len(draws) < 0.25
    assert all(d in (1.0, 51.0) for d in draws)


def test_long_pause_never_stacks_on_a_read_pause():
    profile = BehaviorProfile(long_pause=Range(50.0, 50.0), long_pause_prob=1.0)
    hum, _ = make(profile)
    assert all(hum.delay("read_pause") == 50.0 for _ in range(20))


def test_disabled_profile_never_sleeps():
    hum, clock = make(BehaviorProfile(enabled=False))
    assert hum.delay("post") == 0.0
    assert clock.slept == []


# --- 2. Early give-up -----------------------------------------------------


def test_early_stop_prob_zero_never_stops():
    hum, _ = make(BehaviorProfile(early_stop_prob=0.0))
    assert not any(hum.should_stop_early() for _ in range(500))


def test_early_stop_prob_one_always_stops():
    hum, _ = make(BehaviorProfile(early_stop_prob=1.0))
    assert all(hum.should_stop_early() for _ in range(500))


def test_early_stop_fires_at_expected_rate():
    hum, _ = make(BehaviorProfile(early_stop_prob=0.3), seed=11)
    stops = sum(hum.should_stop_early() for _ in range(2000))
    assert 0.25 < stops / 2000 < 0.35


def test_early_stop_disabled_when_humanization_off():
    hum, _ = make(BehaviorProfile(enabled=False, early_stop_prob=1.0))
    assert not hum.should_stop_early()


def test_scan_limit_zero_is_clamped_only_under_humanization():
    hum, _ = make(BehaviorProfile(scan_depth_clamp=200))
    assert hum.clamp_scan_limit(0) == 200
    assert hum.clamp_scan_limit(50) == 50  # an explicit limit is honored

    plain, _ = make(BehaviorProfile(enabled=False))
    assert plain.clamp_scan_limit(0) == 0  # 0 == all, exactly as today


# --- 3. Rate ceilings & active hours -------------------------------------


def test_gate_proceeds_when_nothing_is_exceeded():
    hum, _ = make()
    assert hum.gate("post").action == PROCEED


def test_rolling_window_ceiling_yields_a_bounded_wait():
    profile = BehaviorProfile(max_requests_per_window=3, window_seconds=600)
    hum, clock = make(profile)
    for _ in range(3):
        hum.record("request")
        clock.t += 10

    result = hum.gate("post")
    assert result.action == WAIT
    assert 0 < result.seconds <= profile.window_seconds  # bounded by the window
    assert "3 requests in the last 600s" in result.reason


def test_window_ceiling_clears_once_the_window_rolls_past():
    profile = BehaviorProfile(max_requests_per_window=2, window_seconds=600)
    hum, clock = make(profile)
    hum.record("request")
    hum.record("request")
    assert hum.gate().action == WAIT
    clock.t += 601
    assert hum.gate().action == PROCEED


def test_session_request_ceiling_stops_rather_than_waits():
    hum, _ = make(BehaviorProfile(max_requests_per_session=2))
    hum.record("request")
    hum.record("request")
    result = hum.gate("post")
    assert result.action == STOP
    assert "session request ceiling" in result.reason


def test_session_post_ceiling_stops_rather_than_waits():
    hum, _ = make(BehaviorProfile(max_posts_per_session=2))
    hum.record("post")
    hum.record("post")
    assert hum.gate("post").action == STOP
    assert hum.gate("request").action == PROCEED  # the cap is per-post only


def test_outside_active_hours_stops_never_waits_for_hours():
    profile = BehaviorProfile(active_hours=(8, 23), active_hours_jitter=Range(0, 0))
    hum, _ = make(profile, at=datetime(2026, 8, 3, 4, 0))
    result = hum.gate("post")
    assert result.action == STOP  # never a multi-hour WAIT
    assert result.seconds == 0.0
    assert "outside active hours 08:00–23:00" in result.reason


def test_inside_active_hours_proceeds():
    profile = BehaviorProfile(active_hours=(8, 23), active_hours_jitter=Range(0, 0))
    hum, _ = make(profile, at=datetime(2026, 8, 3, 12, 0))
    assert hum.gate("post").action == PROCEED


def test_active_hours_none_is_always_in_window():
    profile = BehaviorProfile(active_hours=None)
    hum, _ = make(profile, at=datetime(2026, 8, 3, 3, 0))
    assert hum.gate("post").action == PROCEED


def test_active_hours_window_can_wrap_past_midnight():
    profile = BehaviorProfile(active_hours=(22, 3), active_hours_jitter=Range(0, 0))
    late, _ = make(profile, at=datetime(2026, 8, 3, 23, 30))
    early, _ = make(profile, at=datetime(2026, 8, 3, 1, 0))
    midday, _ = make(profile, at=datetime(2026, 8, 3, 12, 0))
    assert late.gate().action == PROCEED
    assert early.gate().action == PROCEED
    assert midday.gate().action == STOP


def test_active_hours_edge_is_jittered_not_a_hard_clock_tick():
    profile = BehaviorProfile(active_hours=(8, 23), active_hours_jitter=Range(10, 30))
    at = datetime(2026, 8, 3, 8, 5)  # inside the jitter band around 08:00
    verdicts = set()
    for seed in range(40):
        hum, _ = make(profile, seed=seed, at=at)
        verdicts.add(hum.gate().action)
    assert verdicts == {PROCEED, STOP}  # the boundary moves run to run


def test_disabled_profile_never_gates():
    hum, _ = make(BehaviorProfile(enabled=False, max_requests_per_session=0))
    assert hum.gate("post").action == PROCEED


# --- 4. Politeness backoff ------------------------------------------------


def test_backoff_grows_exponentially_and_respects_the_cap():
    profile = BehaviorProfile(backoff_base=60.0, backoff_max=200.0)
    hum, clock = make(profile)
    waits = [hum.backoff(a) for a in range(4)]
    ceilings = [60.0, 120.0, 200.0, 200.0]  # capped at backoff_max
    for wait, ceiling in zip(waits, ceilings):
        assert ceiling / 2 <= wait <= ceiling  # jittered, never over the cap
    assert clock.slept == waits  # went through the injected sleep


def test_backoff_is_jittered_not_a_fixed_schedule():
    profile = BehaviorProfile(backoff_base=60.0)
    values = {make(profile, seed=s)[0].backoff(0) for s in range(20)}
    assert len(values) > 1


def test_can_backoff_gives_up_after_configured_attempts():
    hum, _ = make(BehaviorProfile(backoff_attempts=3))
    assert [hum.can_backoff(a) for a in range(5)] == [True, True, True, False, False]


def test_no_backoff_when_humanization_is_off():
    hum, _ = make(BehaviorProfile(enabled=False))
    assert not hum.can_backoff(0)


# --- warm-up --------------------------------------------------------------


class _FeedClient:
    def __init__(self, boom: bool = False) -> None:
        self.calls = 0
        self.boom = boom

    def get_timeline_feed(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("feed unavailable")
        return {}


def test_warmup_makes_between_zero_and_max_calls():
    profile = BehaviorProfile(warmup_calls=Range(1, 2))
    hum, _ = make(profile)
    client = _FeedClient()
    made = hum.warmup(client)
    assert made == client.calls
    assert 1 <= made <= 2


def test_warmup_disabled_makes_no_calls():
    hum, _ = make(BehaviorProfile(warmup_calls=Range(0, 0)))
    client = _FeedClient()
    assert hum.warmup(client) == 0
    assert client.calls == 0

    off, _ = make(BehaviorProfile(enabled=False, warmup_calls=Range(2, 2)))
    other = _FeedClient()
    assert off.warmup(other) == 0
    assert other.calls == 0


def test_warmup_failure_never_breaks_the_run():
    hum, _ = make(BehaviorProfile(warmup_calls=Range(2, 2)))
    assert hum.warmup(_FeedClient(boom=True)) == 0


# --- 5. Config parsing & profile builder ---------------------------------


def test_build_profile_parses_range_strings():
    profile = build_profile({"humanize_post_delay": "2,7"})
    assert profile.post_delay == Range(2.0, 7.0)


def test_build_profile_defaults_come_from_the_dataclass():
    assert build_profile({}) == BehaviorProfile()


def test_build_profile_parses_scalars_and_active_hours():
    profile = build_profile(
        {
            "humanize_early_stop_prob": "0.5",
            "humanize_max_posts": "12",
            "humanize_active_hours": "9,18",
            "humanize_seed": "42",
        }
    )
    assert profile.early_stop_prob == 0.5
    assert profile.max_posts_per_session == 12
    assert profile.active_hours == (9, 18)
    assert profile.seed == 42


def test_build_profile_active_hours_off_means_anytime():
    for word in ("off", "none", "any"):
        assert build_profile({"humanize_active_hours": word}).active_hours is None


def test_no_humanize_disables_the_profile():
    assert build_profile({"humanize": False}).enabled is False
    assert build_profile({"humanize": None}).enabled is True  # unset == on


@pytest.mark.parametrize(
    "opts,fragment",
    [
        ({"humanize_post_delay": "7"}, 'expected a "lo,hi" range'),
        ({"humanize_post_delay": "a,b"}, "expected two numbers"),
        ({"humanize_post_delay": "9,2"}, "lo must not exceed hi"),
        ({"humanize_early_stop_prob": "soon"}, "expected a number"),
        ({"humanize_max_posts": "lots"}, "expected a whole number"),
        ({"humanize_active_hours": "8"}, "startHour,endHour"),
        ({"humanize_active_hours": "8,30"}, "hours must be 0–23"),
    ],
)
def test_malformed_values_raise_a_clear_error(opts, fragment):
    with pytest.raises(ValueError, match=fragment):
        build_profile(opts)


def test_profile_summary_describes_the_effective_pacing():
    summary = BehaviorProfile(post_delay=Range(20, 90)).summary()
    assert summary.startswith("on ·")
    assert "post 20–90s" in summary
    assert "08:00–23:00" in summary
    assert BehaviorProfile(enabled=False).summary() == "off"


# --- 8. Cross-session state: the activity ledger --------------------------
#
# These use a real `ActivityLedger` in `tmp_path` (no mocking of the code under
# test) with the same fake clock, so a "run" is just a new Humanizer over the
# same file.


def open_ledger(tmp_path, clock, profile=None, **kw):
    profile = profile or BehaviorProfile()
    ledger = ActivityLedger(
        tmp_path / "activity-me.json",
        window_seconds=profile.window_seconds,
        now=clock.now,
        sleep=clock.sleep,
        **kw,
    )
    return ledger.__enter__()


def run(tmp_path, clock, profile=None, seed=1, at=None, ledger=None):
    """One "invocation": a fresh Humanizer over the same ledger file."""
    profile = profile or BehaviorProfile()
    ledger = ledger if ledger is not None else open_ledger(tmp_path, clock, profile)
    wall = (lambda: at) if at is not None else (lambda: datetime(2026, 8, 3, 12, 0))
    hum = Humanizer(
        profile,
        rng=random.Random(seed),
        sleep=clock.sleep,
        now=clock.now,
        wall=wall,
        ledger=ledger,
    )
    return hum, ledger


def test_a_fresh_ledger_is_both_a_new_session_and_a_cold_open(tmp_path):
    hum, _ = run(tmp_path, FakeClock(10_000.0))
    assert hum.is_new_session() and hum.is_cold_open()
    assert hum.owed_idle() == 0.0  # nothing to continue from


def test_a_short_gap_is_neither_a_new_session_nor_a_cold_open(tmp_path):
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t += 90  # 90 s later
    second, _ = run(tmp_path, clock)
    assert not second.is_new_session()
    assert not second.is_cold_open()
    assert second.posts == 1  # the budget carried over


def test_a_mid_range_gap_is_a_cold_open_without_being_a_new_session(tmp_path):
    """The case the two thresholds exist for: 26 min at the defaults."""
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t += 26 * 60
    second, _ = run(tmp_path, clock)
    assert second.is_cold_open()          # the app plainly wasn't open
    assert not second.is_new_session()    # but it's still one sitting
    assert second.posts == 1


def test_a_long_gap_is_both_and_zeroes_the_session_counters(tmp_path):
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    first.record("request")
    ledger.__exit__()

    clock.t += 40 * 60
    second, _ = run(tmp_path, clock)
    assert second.is_new_session() and second.is_cold_open()
    assert (second.posts, second.requests) == (0, 0)


def test_a_backwards_clock_owes_no_idle_and_never_goes_negative(tmp_path):
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t -= 60  # NTP nudged the clock backwards
    second, _ = run(tmp_path, clock)
    assert second._gap == 0.0
    assert second.owed_idle() >= 0.0
    assert not second.is_cold_open()  # a 0 gap is "still open", not a fresh open


def test_a_wildly_future_dated_ledger_starts_fresh_instead_of_idling(tmp_path, capsys):
    """A jump bigger than the window is not a nudge — the file is untrustworthy."""
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t -= 5_000  # last_action is now > window_seconds in the "future"
    second, _ = run(tmp_path, clock)
    assert second._gap is None          # nothing to continue from
    assert second.owed_idle() == 0.0    # and certainly no hours of idle
    assert second.is_cold_open()
    assert "future" in capsys.readouterr().err


def test_the_window_survives_the_process_boundary(tmp_path):
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    for _ in range(3):
        first.record("request")
    first.record("post")  # flushes
    ledger.__exit__()

    second, _ = run(tmp_path, clock)
    assert len(second._window) == 3
    assert second.requests == 3


def test_owed_idle_is_the_remainder_of_a_post_scale_pause(tmp_path):
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t += 2  # a loop firing the next invocation immediately
    second, _ = run(tmp_path, clock)
    owed = second.owed_idle()
    assert 20.0 - 2 <= owed <= (90.0 + 120.0) - 2   # post_delay [+ long_pause] − gap
    assert owed > 0


def test_owed_idle_is_zero_once_the_gap_is_long_enough(tmp_path):
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t += 3600
    second, _ = run(tmp_path, clock)
    assert second.owed_idle() == 0.0


def test_owed_idle_reaches_the_long_pause_tail(tmp_path):
    """Sampling bare `post_delay` could never exceed 90 s; the pace can."""
    profile = BehaviorProfile(long_pause_prob=1.0)
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock, profile)
    first.record("post")
    ledger.__exit__()

    clock.t += 1
    second, _ = run(tmp_path, clock, profile)
    assert second.owed_idle() > profile.post_delay.hi


def test_owed_idle_is_zero_when_humanization_is_off(tmp_path):
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t += 2
    second, _ = run(tmp_path, clock, BehaviorProfile(enabled=False))
    assert second.owed_idle() == 0.0


def test_delay_sleeps_exactly_what_sample_delay_returns():
    """The split is a pure refactor: same seed, same draw, same sleep."""
    sampler, sampler_clock = make(seed=7)
    sleeper, sleeper_clock = make(seed=7)
    expected = sampler.sample_delay("post")
    assert sleeper.delay("post") == expected
    assert sleeper_clock.slept == [expected]
    assert sampler_clock.slept == []


def test_sample_delay_does_not_sleep():
    hum, clock = make()
    hum.sample_delay("post")
    assert clock.slept == []


# --- day ceilings ---------------------------------------------------------


def test_the_day_ceiling_stops_rather_than_waits(tmp_path):
    profile = BehaviorProfile(max_posts_per_day=2)
    clock = FakeClock(10_000.0)
    hum, _ = run(tmp_path, clock, profile)
    hum.record("post")
    hum.record("post")
    result = hum.gate("post")
    assert result.action == STOP
    assert "daily post ceiling" in result.reason


def test_the_day_request_ceiling_binds_across_invocations(tmp_path):
    profile = BehaviorProfile(max_requests_per_day=3)
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock, profile)
    for _ in range(3):
        first.record("request")
    first.record("post")
    ledger.__exit__()

    clock.t += 40 * 60  # a new *session*, but the same day
    second, _ = run(tmp_path, clock, profile)
    assert second.requests == 0                     # session counters reset
    assert second.gate("request").action == STOP     # the day's budget did not
    assert "daily request ceiling" in second.gate("request").reason


def test_day_counters_roll_over_at_local_midnight(tmp_path):
    profile = BehaviorProfile(max_posts_per_day=1)
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock, profile, at=datetime(2026, 8, 3, 22, 0))
    first.record("post")
    assert first.gate("post").action == STOP
    ledger.__exit__()

    clock.t += 7200  # two hours later — the next local day
    second, _ = run(tmp_path, clock, profile, at=datetime(2026, 8, 4, 9, 0))
    assert second.gate("post").action == PROCEED
    assert second._activity.day_posts == 0


# --- stable daily edges ---------------------------------------------------


def test_the_active_hours_edge_is_stable_for_the_day(tmp_path):
    clock = FakeClock(10_000.0)
    ledger = open_ledger(tmp_path, clock)
    shifts = {
        (run(tmp_path, clock, seed=s, ledger=ledger)[0]._open_shift,
         run(tmp_path, clock, seed=s, ledger=ledger)[0]._close_shift)
        for s in range(5)
    }
    assert len(shifts) == 1, "the boundary must not flicker run to run"


def test_the_active_hours_edge_moves_tomorrow(tmp_path):
    clock = FakeClock(10_000.0)
    ledger = open_ledger(tmp_path, clock)
    today, _ = run(tmp_path, clock, at=datetime(2026, 8, 3, 12, 0), ledger=ledger)
    tomorrow, _ = run(tmp_path, clock, at=datetime(2026, 8, 4, 12, 0), ledger=ledger)
    assert today._open_shift != tomorrow._open_shift


def test_the_derived_edge_stays_inside_the_configured_jitter(tmp_path):
    clock = FakeClock(10_000.0)
    profile = BehaviorProfile(active_hours_jitter=Range(0.0, 30.0))
    for day in range(1, 29):
        ledger = open_ledger(tmp_path / str(day), clock, profile)
        hum, _ = run(tmp_path, clock, profile, at=datetime(2026, 8, day, 12, 0),
                     ledger=ledger)
        for shift in (hum._open_shift, hum._close_shift):
            assert abs(shift) <= 30.0 / 60.0


def test_without_a_ledger_the_edge_is_still_drawn_from_the_rng():
    """The no-ledger path must reproduce the pre-ledger behavior exactly."""
    a, _ = make(seed=3)
    b, _ = make(seed=3)
    assert (a._open_shift, a._close_shift) == (b._open_shift, b._close_shift)
    c, _ = make(seed=4)
    assert c._open_shift != a._open_shift


# --- recording, and surviving the exit ------------------------------------


def test_recording_a_post_reaches_the_disk(tmp_path):
    clock = FakeClock(10_000.0)
    hum, ledger = run(tmp_path, clock)
    hum.record("post")
    saved = json.loads((tmp_path / "activity-me.json").read_text())
    assert saved["session_posts"] == 1 and saved["day_posts"] == 1
    assert saved["last_action"] == clock.t


def test_recording_a_request_does_not_flush_per_request(tmp_path):
    clock = FakeClock(10_000.0)
    hum, ledger = run(tmp_path, clock)
    hum.record("request")
    assert not (tmp_path / "activity-me.json").exists()  # no flush yet
    assert hum.requests == 1                             # but the state is live
    ledger.__exit__()
    assert json.loads((tmp_path / "activity-me.json").read_text())["session_requests"] == 1


def test_record_is_unconditional_even_when_humanization_is_off(tmp_path):
    """Accounting is not pacing: `--no-humanize` must not lie to the next run."""
    clock = FakeClock(10_000.0)
    hum, ledger = run(tmp_path, clock, BehaviorProfile(enabled=False))
    hum.record("post")
    hum.record("request")
    ledger.__exit__()
    saved = json.loads((tmp_path / "activity-me.json").read_text())
    assert (saved["day_posts"], saved["day_requests"]) == (1, 1)
    assert saved["last_action"] == clock.t


def test_the_mixed_workflow_trap(tmp_path):
    """humanized → unhumanized → humanized: the third run must see the truth."""
    clock = FakeClock(10_000.0)
    first, ledger = run(tmp_path, clock)
    first.record("post")
    ledger.__exit__()

    clock.t += 60
    unpaced, ledger = run(tmp_path, clock, BehaviorProfile(enabled=False))
    for _ in range(5):
        unpaced.record("post")
    ledger.__exit__()

    clock.t += 60
    third, _ = run(tmp_path, clock)
    assert third._activity.day_posts == 6      # the burst is counted
    assert not third.is_cold_open()            # …and the gap is not stale

    # With the ledger switched off for the middle run, that is the user's choice:
    clock2 = FakeClock(50_000.0)
    a, ledger_a = run(tmp_path / "other", clock2)
    a.record("post")
    ledger_a.__exit__()
    off = ActivityLedger(
        tmp_path / "other" / "activity-me.json",
        window_seconds=3600, now=clock2.now, sleep=clock2.sleep, enabled=False,
    )
    with off:
        blind = Humanizer(BehaviorProfile(enabled=False), rng=random.Random(1),
                          sleep=clock2.sleep, now=clock2.now,
                          wall=lambda: datetime(2026, 8, 3, 12, 0), ledger=off)
        blind.record("post")
    clock2.t += 60
    later, _ = run(tmp_path / "other", clock2)
    assert later._activity.day_posts == 1      # the unrecorded burst is invisible


def test_a_disabled_ledger_reproduces_the_pre_ledger_humanizer(tmp_path):
    clock = FakeClock(10_000.0)
    off = ActivityLedger(
        tmp_path / "activity-me.json", window_seconds=3600,
        now=clock.now, sleep=clock.sleep, enabled=False,
    )
    with off:
        hum = Humanizer(BehaviorProfile(), rng=random.Random(1), sleep=clock.sleep,
                        now=clock.now, wall=lambda: datetime(2026, 8, 3, 12, 0),
                        ledger=off)
        plain, _ = make(seed=1)
        assert hum.is_cold_open() and hum.is_new_session()
        assert hum.owed_idle() == 0.0
        assert hum._open_shift == plain._open_shift  # RNG-drawn, as before
        hum.record("post")
    assert list(tmp_path.iterdir()) == []


# --- the two thresholds are validated, not assumed ------------------------


def test_an_inverted_idle_config_is_corrected_with_a_warning(capsys):
    profile = build_profile({
        "humanize_session_idle_reset": 300,
        "humanize_foreground_idle": 1800,
    })
    assert profile.foreground_idle == profile.session_idle_reset == 300
    assert "raising it to the reset" in capsys.readouterr().err


def test_the_new_idle_and_day_options_come_through_build_profile():
    profile = build_profile({
        "humanize_session_idle_reset": "600",
        "humanize_foreground_idle": "60",
        "humanize_max_requests_per_day": "42",
        "humanize_max_posts_per_day": "7",
    })
    assert (profile.session_idle_reset, profile.foreground_idle) == (600.0, 60.0)
    assert (profile.max_requests_per_day, profile.max_posts_per_day) == (42, 7)


# --- provenance -----------------------------------------------------------


def test_the_pacing_summary_states_whether_the_ledger_was_in_use(tmp_path):
    clock = FakeClock(10_000.0)
    with_ledger, _ = run(tmp_path, clock)
    assert with_ledger.pacing_summary().endswith("· ledger on")
    assert with_ledger.profile.summary() in with_ledger.pacing_summary()

    without, _ = make()
    assert without.pacing_summary().endswith("· ledger off")


def test_an_unhumanized_run_still_reports_off(tmp_path):
    clock = FakeClock(10_000.0)
    unpaced, _ = run(tmp_path, clock, BehaviorProfile(enabled=False))
    assert unpaced.pacing_summary() == "off"  # the ledger is accounting, not pacing

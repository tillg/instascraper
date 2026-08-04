"""Network-free, sleep-free tests for the behavior profile and humanizer.

Every test injects a seeded RNG and a fake clock, so nothing here blocks and
nothing depends on wall-clock time.
"""

from __future__ import annotations

import random
from datetime import datetime

import pytest

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

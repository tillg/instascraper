"""Human-behavior simulation: one place that owns every pacing decision.

`BehaviorProfile` is pure data — every tunable the tool has (delay ranges,
probabilities, depth clamp, rate ceilings, active hours, backoff). `Humanizer`
applies it: samples think-times, decides probabilistic early-stops, enforces
rate ceilings, and computes politeness backoff. Call sites in `scraper.py`,
`cli.py`, and `auth.py` ask the humanizer *when* and *how long* to wait; they
never hold timing constants themselves.

The RNG and the clock are injected, so tests are deterministic and never sleep.

Pacing state is *account*-scoped, not process-scoped: an `activity.ActivityLedger`
carries the counters, the rolling window, and the last action across invocations,
so ten sequential runs pace like one batch instead of ten fresh starts. Policy
still lives here; the ledger owns the file.
"""

from __future__ import annotations

import hashlib
import random
import sys
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime

from .activity import Activity


@dataclass(frozen=True)
class Range:
    """An inclusive `lo..hi` band, sampled per use — never a fixed constant."""

    lo: float
    hi: float

    def sample(self, rng: random.Random) -> float:
        return rng.uniform(self.lo, self.hi)

    def sample_int(self, rng: random.Random) -> int:
        return rng.randint(int(self.lo), int(self.hi))

    def __str__(self) -> str:
        return f"{self.lo:g}–{self.hi:g}"


# Defaults calibrated against the live capture in
# specs/system/observations-web-cadence.md: a real session is
# short bursts separated by long, high-variance idle (observed 4.4s → 22s →
# 57s between actions), and comments get a screenful of reading then abandoned.
@dataclass(frozen=True)
class BehaviorProfile:
    """Every way the tool paces itself, in one readable dataclass."""

    enabled: bool = True
    request_delay: Range = Range(1.0, 4.0)      # between private API calls
    page_delay: Range = Range(2.0, 8.0)         # between comment pages
    post_delay: Range = Range(20.0, 90.0)       # between posts — dominant idle
    long_pause: Range = Range(30.0, 120.0)      # occasional "distracted" gap
    long_pause_prob: float = 0.2
    early_stop_prob: float = 0.3                # per-page chance to stop reading
    warmup_calls: Range = Range(0, 2)           # app-open calls (sample_int)
    scan_depth_clamp: int = 200                 # what `--comment-scan-limit 0` becomes
    max_requests_per_session: int = 300
    max_posts_per_session: int = 60
    window_seconds: int = 3600                  # rolling window
    max_requests_per_window: int = 200
    # Day ceilings: the payoff of a persisted ledger — a daily budget is
    # meaningless without one. These two are *accepted guesses*, not calibration:
    # the live capture is a single session and says nothing about daily volume.
    max_requests_per_day: int = 1000
    max_posts_per_day: int = 150
    # Two thresholds read from the *same* idle gap, answering two questions.
    session_idle_reset: float = 1800.0          # "same sitting?" — budget carries
    foreground_idle: float = 300.0              # "app still open?" — warm-up fires
    active_hours: tuple[int, int] | None = (8, 23)  # local; None = anytime
    active_hours_jitter: Range = Range(0.0, 30.0)   # minutes; drawn once per run
    backoff_base: float = 60.0
    backoff_max: float = 900.0
    backoff_attempts: int = 3
    seed: int | None = None                     # set in tests for determinism

    def summary(self) -> str:
        """One-line description of the effective pacing, for provenance."""
        if not self.enabled:
            return "off"
        hours = (
            "any hours" if self.active_hours is None
            else f"{self.active_hours[0]:02d}:00–{self.active_hours[1]:02d}:00"
        )
        return (
            f"on · request {self.request_delay}s · page {self.page_delay}s · "
            f"post {self.post_delay}s · long-pause p={self.long_pause_prob:g} "
            f"({self.long_pause}s) · early-stop p={self.early_stop_prob:g} · {hours}"
        )


# Gate verdicts.
PROCEED = "proceed"
WAIT = "wait"
STOP = "stop"


@dataclass(frozen=True)
class GateResult:
    """What `Humanizer.gate()` says about starting the next action.

    `WAIT` is only ever issued for the rolling-window ceiling, so it is bounded
    by `window_seconds`. Session ceilings and being outside active hours return
    `STOP` — the tool never silently sleeps for hours.
    """

    action: str            # PROCEED | WAIT | STOP
    seconds: float = 0.0
    reason: str = ""


_PROCEED = GateResult(PROCEED)

# Action kind -> the profile range it samples from.
_RANGE_FOR = {
    "request": "request_delay",
    "warmup": "request_delay",
    "page": "page_delay",
    "post": "post_delay",
    "read_pause": "long_pause",
}


class Humanizer:
    """Applies a `BehaviorProfile`. Holds the RNG, the clock, and the counters."""

    def __init__(
        self,
        profile: BehaviorProfile | None = None,
        rng: random.Random | None = None,
        sleep=time.sleep,
        now=time.time,
        wall=datetime.now,
        ledger=None,
    ) -> None:
        self.profile = profile if profile is not None else BehaviorProfile()
        self._rng = rng if rng is not None else random.Random(self.profile.seed)
        self._sleep = sleep
        self._now = now
        self._wall = wall
        # `now` is wall-clock (UTC epoch), not monotonic: monotonic values are
        # meaningless in another process, so the window could not be persisted.
        # The consequences (backwards clock, future timestamps) are handled
        # explicitly instead — see `_gap` and `ActivityLedger.load`.
        self._ledger = ledger
        self._activity: Activity = ledger.activity if ledger is not None else Activity()

        activity = self._activity
        # One measurement of the idle gap, read twice. `None` = nothing to
        # continue from (fresh ledger), which is both a new session and a cold
        # open. A backwards clock floors at 0 rather than owing negative idle.
        self._gap: float | None = (
            max(0.0, self._now() - activity.last_action)
            if activity.last_action > 0.0 else None
        )
        if self.is_new_session():
            activity.session_requests = 0
            activity.session_posts = 0
        self._roll_day()
        self.requests = activity.session_requests
        self.posts = activity.session_posts
        self._window: deque[float] = deque(activity.window)
        # Soften the active-hours boundary so it isn't the same clock tick every
        # run — but derive it, so it sits in one place all day instead of
        # flickering run to run. Falls back to an RNG draw with no ledger.
        self._open_shift = self._edge_shift("open")
        self._close_shift = self._edge_shift("close")

    def _edge_shift(self, which: str) -> float:
        """A signed active-hours boundary shift, in hours.

        Derived from `(ledger salt, local date)` so the boundary is stable for
        the day and different tomorrow: a person whose bedtime is 23:14 today,
        not a boundary that moves every invocation. With no ledger there is no
        salt, and it falls back to today's RNG draw.
        """
        jitter = self.profile.active_hours_jitter
        salt = self._activity.salt
        if not salt:
            magnitude = jitter.sample(self._rng)
            return self._rng.choice((-1.0, 1.0)) * magnitude / 60.0
        digest = hashlib.sha256(f"{salt}:{self._today()}:{which}".encode()).digest()
        frac = int.from_bytes(digest[:8], "big") / 2 ** 64          # [0,1)
        magnitude = jitter.lo + frac * (jitter.hi - jitter.lo)
        return (1.0 if digest[8] & 1 else -1.0) * magnitude / 60.0

    # --- activity sessions -------------------------------------------------

    def _today(self) -> str:
        return self._wall().date().isoformat()

    def _roll_day(self) -> None:
        """Day counters belong to a *local* date; reset them when it changes."""
        activity = self._activity
        today = self._today()
        if activity.day != today:
            activity.day, activity.day_requests, activity.day_posts = today, 0, 0

    def is_new_session(self) -> bool:
        """Is this a fresh sitting — do the session ceilings start over?"""
        return self._gap is None or self._gap > self.profile.session_idle_reset

    def is_cold_open(self) -> bool:
        """Was the app plausibly *not* still open, so this run opens it?

        A much shorter horizon than the session boundary: a 26-minute gap is one
        sitting for budget purposes but unquestionably a fresh app-open. Minting
        a session is also a cold open — that call is `auth.get_client`'s, since a
        login is an app-open whatever the gap.
        """
        return self._gap is None or self._gap > self.profile.foreground_idle

    def owed_idle(self) -> float:
        """Seconds still to wait before this run's *first request of any kind*.

        The gap this stands in for is the inter-post pace, so it samples the very
        same distribution (`delay("post")`, long-pause tail included) and
        subtracts what already elapsed. Zero with no ledger, on a fresh one, or
        once the gap is long enough. Paid before login, because the
        session-validation request is already a real request.
        """
        if not self.profile.enabled or self._gap is None:
            return 0.0
        return max(0.0, self.sample_delay("post") - self._gap)

    # --- think-time ------------------------------------------------------

    def sample_delay(self, kind: str = "request") -> float:
        """The think-time for `kind`, sampled but *not* slept.

        Split out of `delay()` so `owed_idle()` can draw from the exact same
        distribution as the pace it stands in for — bare `post_delay` would run
        ~20% short and would never produce the long tail at all.
        """
        if not self.profile.enabled:
            return 0.0
        attr = _RANGE_FOR.get(kind)
        if attr is None:
            raise ValueError(
                f"Unknown action kind {kind!r}; expected one of {', '.join(_RANGE_FOR)}"
            )
        seconds = getattr(self.profile, attr).sample(self._rng)
        # The tail an even drip never produces: the user got distracted.
        if kind != "read_pause" and self._rng.random() < self.profile.long_pause_prob:
            seconds += self.profile.long_pause.sample(self._rng)
        return seconds

    def delay(self, kind: str = "request") -> float:
        """Sleep a sampled think-time for `kind`; return the seconds slept."""
        seconds = self.sample_delay(kind)
        if seconds > 0:  # a disabled profile samples 0 and must not sleep at all
            self._sleep(seconds)
        return seconds

    def wait(self, seconds: float) -> None:
        """Sleep an externally-decided duration (e.g. a gate's WAIT)."""
        if seconds > 0:
            self._sleep(seconds)

    # --- human-scale depth -----------------------------------------------

    def should_stop_early(self) -> bool:
        """True when a human would have stopped reading and moved on."""
        if not self.profile.enabled:
            return False
        return self._rng.random() < self.profile.early_stop_prob

    def clamp_scan_limit(self, scan_limit: int) -> int:
        """`--comment-scan-limit 0` (= all) is not a human-scale depth.

        Under humanization it becomes `scan_depth_clamp`; unhumanized runs keep
        today's `0 = all` exactly.
        """
        if not self.profile.enabled:
            return scan_limit
        return self.profile.scan_depth_clamp if scan_limit <= 0 else scan_limit

    # --- rate ceilings ----------------------------------------------------

    def record(self, kind: str = "request") -> None:
        """Log an action against the counters, the window, and the ledger.

        Deliberately *not* short-circuited on `profile.enabled`: accounting is
        not pacing. `--no-humanize` stops the waiting and the gating, but a run
        that acts unpaced and records nothing leaves the ledger stating a
        falsehood, and the next run then paces against it. Only
        `--no-activity-ledger` stops the file.
        """
        self._roll_day()
        activity = self._activity
        now = self._now()
        if kind == "post":
            self.posts += 1
            activity.day_posts += 1
        else:
            self.requests += 1
            activity.day_requests += 1
            self._window.append(now)
        activity.last_action = now
        activity.session_requests = self.requests
        activity.session_posts = self.posts
        activity.window = list(self._window)
        if kind == "post" and self._ledger is not None:
            # Flush per post, not per request: one `os.replace` next to a
            # multi-second paced fetch is free, and a killed batch — exactly when
            # the state matters — then loses at most one post's worth of budget.
            self._ledger.flush()

    def gate(self, kind: str = "request") -> GateResult:
        """Verdict on starting the next action. See `GateResult`.

        Checked cheapest-and-most-final first: active hours, then the day
        ceilings, then the session ceilings, then the rolling window — the only
        one that ever yields a bounded `WAIT`.
        """
        p = self.profile
        if not p.enabled:
            return _PROCEED

        if not self._within_active_hours():
            lo, hi = p.active_hours  # type: ignore[misc]  # None short-circuits above
            return GateResult(
                STOP,
                reason=f"outside active hours {lo:02d}:00–{hi:02d}:00",
            )
        self._roll_day()
        activity = self._activity
        if activity.day_requests >= p.max_requests_per_day:
            return GateResult(
                STOP,
                reason=f"daily request ceiling reached ({p.max_requests_per_day})",
            )
        if kind == "post" and activity.day_posts >= p.max_posts_per_day:
            return GateResult(
                STOP,
                reason=f"daily post ceiling reached ({p.max_posts_per_day})",
            )
        if self.requests >= p.max_requests_per_session:
            return GateResult(
                STOP,
                reason=f"session request ceiling reached ({p.max_requests_per_session})",
            )
        if kind == "post" and self.posts >= p.max_posts_per_session:
            return GateResult(
                STOP,
                reason=f"session post ceiling reached ({p.max_posts_per_session})",
            )

        self._prune_window()
        if len(self._window) >= p.max_requests_per_window:
            elapsed = self._now() - self._window[0]
            return GateResult(
                WAIT,
                seconds=max(p.window_seconds - elapsed, 0.0),
                reason=(
                    f"{p.max_requests_per_window} requests in the last "
                    f"{p.window_seconds}s"
                ),
            )
        return _PROCEED

    def _prune_window(self) -> None:
        cutoff = self._now() - self.profile.window_seconds
        while self._window and self._window[0] <= cutoff:
            self._window.popleft()

    def _within_active_hours(self) -> bool:
        if self.profile.active_hours is None:
            return True
        start, end = self.profile.active_hours
        opens = start + self._open_shift
        closes = end + self._close_shift
        now = self._wall()
        hour = now.hour + now.minute / 60 + now.second / 3600
        if opens <= closes:
            return opens <= hour < closes
        return hour >= opens or hour < closes  # window wraps midnight

    # --- politeness -------------------------------------------------------

    def can_backoff(self, attempt: int) -> bool:
        """True while `attempt` is still within `backoff_attempts`."""
        return self.profile.enabled and attempt < self.profile.backoff_attempts

    def backoff(self, attempt: int) -> float:
        """Sleep a jittered exponential backoff; return the seconds slept.

        `backoff_base * 2**attempt`, capped at `backoff_max`, then jittered
        downward so retries from parallel runs don't align. Never exceeds the cap.
        """
        p = self.profile
        ceiling = min(p.backoff_base * (2 ** attempt), p.backoff_max)
        seconds = self._rng.uniform(ceiling / 2, ceiling)
        self._sleep(seconds)
        return seconds

    # --- provenance --------------------------------------------------------

    def pacing_summary(self) -> str:
        """How this run was paced, for `Provenance.humanization`.

        Composed here rather than in `BehaviorProfile.summary()`: the ledger is a
        collaborator, not profile data, and the frozen dataclass stays pure. The
        distinction matters to a reader of `post.md` — a run without the ledger
        got no cross-session idle and no shared ceilings.
        """
        summary = self.profile.summary()
        if not self.profile.enabled:
            return summary
        return f"{summary} · ledger {'on' if self._ledger is not None else 'off'}"

    # --- warm-up ----------------------------------------------------------

    def warmup(self, client) -> int:
        """Open-the-app calls before the first real fetch; returns the count made.

        Warm-up is cosmetic — a failure here must never fail the run.
        """
        if not self.profile.enabled:
            return 0
        made = 0
        for _ in range(self.profile.warmup_calls.sample_int(self._rng)):
            self.delay("warmup")
            try:
                client.get_timeline_feed()
            except Exception:
                break
            self.record("request")
            made += 1
        return made


# --- configuration -------------------------------------------------------

# Option key (as returned by cli.resolve_options) -> BehaviorProfile field.
_RANGE_OPTS = {
    "humanize_request_delay": "request_delay",
    "humanize_page_delay": "page_delay",
    "humanize_post_delay": "post_delay",
    "humanize_long_pause": "long_pause",
    "humanize_warmup_calls": "warmup_calls",
    "humanize_active_hours_jitter": "active_hours_jitter",
}
_FLOAT_OPTS = {
    "humanize_session_idle_reset": "session_idle_reset",
    "humanize_foreground_idle": "foreground_idle",
    "humanize_long_pause_prob": "long_pause_prob",
    "humanize_early_stop_prob": "early_stop_prob",
    "humanize_backoff_base": "backoff_base",
    "humanize_backoff_max": "backoff_max",
}
_INT_OPTS = {
    "humanize_scan_depth": "scan_depth_clamp",
    "humanize_max_requests_per_day": "max_requests_per_day",
    "humanize_max_posts_per_day": "max_posts_per_day",
    "humanize_max_requests": "max_requests_per_session",
    "humanize_max_posts": "max_posts_per_session",
    "humanize_window_seconds": "window_seconds",
    "humanize_max_requests_per_window": "max_requests_per_window",
    "humanize_backoff_attempts": "backoff_attempts",
    "humanize_seed": "seed",
}

_OFF_WORDS = {"off", "none", "any", "always"}


def _parse_range(key: str, raw) -> Range:
    if isinstance(raw, Range):
        return raw
    parts = [p.strip() for p in str(raw).split(",")]
    if len(parts) != 2:
        raise ValueError(f'{key}: expected a "lo,hi" range (e.g. "2,7"), got {raw!r}')
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(
            f'{key}: expected two numbers in "lo,hi" (e.g. "2,7"), got {raw!r}'
        ) from None
    if lo > hi:
        raise ValueError(f"{key}: lo must not exceed hi, got {raw!r}")
    return Range(lo, hi)


def _parse_number(key: str, raw, cast, what: str):
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: expected {what}, got {raw!r}") from None


def _parse_active_hours(raw) -> tuple[int, int] | None:
    if raw is None or isinstance(raw, tuple):
        return raw
    text = str(raw).strip()
    if text.lower() in _OFF_WORDS or not text:
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        raise ValueError(
            'humanize_active_hours: expected "startHour,endHour" (e.g. "8,23") '
            f'or "off", got {raw!r}'
        )
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(
            f'humanize_active_hours: hours must be whole numbers, got {raw!r}'
        ) from None
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise ValueError(f"humanize_active_hours: hours must be 0–23, got {raw!r}")
    return (start, end)


def build_profile(opts: dict) -> BehaviorProfile:
    """Build a `BehaviorProfile` from resolved CLI/`.env`/env options.

    Only options the user actually set appear in `opts` as non-`None`; every
    other value falls through to the dataclass default, so `BehaviorProfile` is
    the single source of default truth. Raises `ValueError` on a malformed value.
    """
    if opts.get("humanize") is False:
        return BehaviorProfile(enabled=False)

    changes: dict = {}
    for key, field_name in _RANGE_OPTS.items():
        if opts.get(key) is not None:
            changes[field_name] = _parse_range(key, opts[key])
    for key, field_name in _FLOAT_OPTS.items():
        if opts.get(key) is not None:
            changes[field_name] = _parse_number(key, opts[key], float, "a number")
    for key, field_name in _INT_OPTS.items():
        if opts.get(key) is not None:
            changes[field_name] = _parse_number(key, opts[key], int, "a whole number")
    if opts.get("humanize_active_hours") is not None:
        changes["active_hours"] = _parse_active_hours(opts["humanize_active_hours"])

    profile = replace(BehaviorProfile(), **changes)
    if profile.foreground_idle > profile.session_idle_reset:
        # "Was the app open?" is necessarily a shorter horizon than "is this the
        # same sitting?". Inverted, a new session would not be a cold open.
        print(
            f"  ! humanize_foreground_idle ({profile.foreground_idle:g}s) exceeds "
            f"humanize_session_idle_reset ({profile.session_idle_reset:g}s); "
            "raising it to the reset.",
            file=sys.stderr,
        )
        profile = replace(profile, foreground_idle=profile.session_idle_reset)
    return profile

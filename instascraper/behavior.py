"""Human-behavior simulation: one place that owns every pacing decision.

`BehaviorProfile` is pure data — every tunable the tool has (delay ranges,
probabilities, depth clamp, rate ceilings, active hours, backoff). `Humanizer`
applies it: samples think-times, decides probabilistic early-stops, enforces
rate ceilings, and computes politeness backoff. Call sites in `scraper.py`,
`cli.py`, and `auth.py` ask the humanizer *when* and *how long* to wait; they
never hold timing constants themselves.

The RNG and the clock are injected, so tests are deterministic and never sleep.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime


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
# specs/changes/human-behavior-simulation/observations.md: a real session is
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
        now=time.monotonic,
        wall=datetime.now,
    ) -> None:
        self.profile = profile if profile is not None else BehaviorProfile()
        self._rng = rng if rng is not None else random.Random(self.profile.seed)
        self._sleep = sleep
        self._now = now
        self._wall = wall
        self._window: deque[float] = deque()
        self.requests = 0
        self.posts = 0
        # Soften the active-hours boundary so it isn't the same clock tick every
        # run. Drawn once per session: the window shifts, it doesn't flicker.
        self._open_shift = self._edge_shift()
        self._close_shift = self._edge_shift()

    def _edge_shift(self) -> float:
        """A signed active-hours boundary shift, in hours."""
        magnitude = self.profile.active_hours_jitter.sample(self._rng)
        return self._rng.choice((-1.0, 1.0)) * magnitude / 60.0

    # --- think-time ------------------------------------------------------

    def delay(self, kind: str = "request") -> float:
        """Sleep a sampled think-time for `kind`; return the seconds slept."""
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
        """Log an action against the session counters and the rolling window."""
        if kind == "post":
            self.posts += 1
            return
        self.requests += 1
        self._window.append(self._now())

    def gate(self, kind: str = "request") -> GateResult:
        """Verdict on starting the next action. See `GateResult`."""
        p = self.profile
        if not p.enabled:
            return _PROCEED

        if not self._within_active_hours():
            lo, hi = p.active_hours  # type: ignore[misc]  # None short-circuits above
            return GateResult(
                STOP,
                reason=f"outside active hours {lo:02d}:00–{hi:02d}:00",
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
    "humanize_long_pause_prob": "long_pause_prob",
    "humanize_early_stop_prob": "early_stop_prob",
    "humanize_backoff_base": "backoff_base",
    "humanize_backoff_max": "backoff_max",
}
_INT_OPTS = {
    "humanize_scan_depth": "scan_depth_clamp",
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

    return replace(BehaviorProfile(), **changes)

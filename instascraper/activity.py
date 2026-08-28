"""Cross-session pacing state: one small ledger per account.

`behavior.py` owns *policy* and stays pure; this module owns *persistence*. The
ledger holds only what pacing needs to be continuous across invocations —
timestamps, counters, and a salt. Never a URL, a shortcode, or any content: that
already lives in the output folders, and duplicating it here would be a privacy
regression.

State is a convenience, never a dependency: a missing, corrupt, truncated, or
future-dated ledger warns and degrades to a fresh one rather than failing a run.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import CONFIG_DIR

try:  # POSIX only; macOS and Linux are the supported platforms.
    import fcntl
except ImportError:  # pragma: no cover - exercised by the no-flock test
    fcntl = None  # type: ignore[assignment]

LEDGER_VERSION = 1
DEFAULT_LOCK_TIMEOUT = 5.0
_LOCK_POLL = 0.1


class LedgerBusy(RuntimeError):
    """Another run holds this account's ledger. A person has one phone."""


def _warn(message: str) -> None:
    print(f"  ! {message}", file=sys.stderr)


@dataclass
class Activity:
    """Everything persisted. Timestamps are UTC epoch seconds (`time.time`)."""

    version: int = LEDGER_VERSION
    salt: str = ""                    # per-account, generated once
    last_action: float = 0.0
    session_requests: int = 0
    session_posts: int = 0
    day: str = ""                     # local ISO date the day counters belong to
    day_requests: int = 0
    day_posts: int = 0
    window: list[float] = field(default_factory=list)   # request epochs

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw) -> "Activity":
        """Never raises. Anything unexpected yields a fresh `Activity`."""
        if not isinstance(raw, dict) or raw.get("version") != LEDGER_VERSION:
            return cls()
        fresh = cls()
        try:
            return cls(
                salt=str(raw.get("salt") or ""),
                last_action=float(raw.get("last_action") or 0.0),
                session_requests=int(raw.get("session_requests") or 0),
                session_posts=int(raw.get("session_posts") or 0),
                day=str(raw.get("day") or ""),
                day_requests=int(raw.get("day_requests") or 0),
                day_posts=int(raw.get("day_posts") or 0),
                window=[float(t) for t in (raw.get("window") or [])],
            )
        except (TypeError, ValueError):
            return fresh


def activity_path(username: str | None, override: str | None = None) -> Path:
    """Where this account's ledger lives.

    Keyed on the *configured* username with the same fallback
    `auth._settings_path` uses for the session file, because the ledger has to
    open before `get_client` can report the account. Rooted at
    `config.CONFIG_DIR` rather than `auth.DEFAULT_SESSION_DIR`: the ledger opens
    before authentication, so this module must not import `auth`.
    """
    if override:
        return Path(override)
    name = f"activity-{username}.json" if username else "activity.json"
    return CONFIG_DIR / name


class ActivityLedger:
    """The account's pacing state on disk, locked for the duration of a run.

    Everything `__enter__` needs is a constructor argument — the pruning
    horizon, the lock bound, and *both* time sources, so the lock retry is
    driven by an injected `sleep` and tests never really wait.
    """

    def __init__(
        self,
        path,
        *,
        window_seconds: int,
        lock_timeout: float | None = None,
        now=time.time,
        sleep=time.sleep,
        enabled: bool = True,
    ) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.activity = Activity()
        self._window_seconds = window_seconds
        # `None` = "not set", so callers can pass a resolved option straight
        # through without having to know the default. `0` stays 0 (fail fast).
        self._lock_timeout = (
            DEFAULT_LOCK_TIMEOUT if lock_timeout is None else lock_timeout
        )
        self._now = now
        self._sleep = sleep
        self._lock_handle = None

    # --- lifecycle --------------------------------------------------------

    def __enter__(self) -> "ActivityLedger":
        if not self.enabled:
            return self  # null object: fresh state, no disk, no lock
        self._acquire()
        self.load()
        return self

    def __exit__(self, *exc) -> None:
        if self.enabled:
            self.flush()
        self.close()

    def close(self) -> None:
        """Release the lock. Safe to call twice; the OS also releases on crash."""
        handle = self._lock_handle
        self._lock_handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()

    # --- the run lock -----------------------------------------------------

    @property
    def lock_path(self) -> Path:
        """A sibling file, *not* the ledger itself.

        `flush()` replaces the ledger's inode (temp + `os.replace`), which would
        pull the locked inode out from under us and let a second run lock the
        replacement. The lock lives on a file nothing ever replaces.
        """
        return self.path.with_name(self.path.name + ".lock")

    def _acquire(self) -> None:
        if fcntl is None:  # pragma: no cover - platform-dependent
            _warn(
                "no fcntl.flock on this platform; the activity ledger runs "
                "unlocked, so don't run two instascrapes for one account at once."
            )
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+")
        try:
            os.chmod(self.lock_path, 0o600)
        except OSError:
            pass
        deadline = self._now() + self._lock_timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_handle = handle
                return
            except OSError:
                if self._now() >= deadline:
                    handle.close()
                    raise LedgerBusy(str(self.lock_path)) from None
                self._sleep(_LOCK_POLL)

    # --- load / save ------------------------------------------------------

    def load(self) -> Activity:
        """Read, validate, prune. Warns and starts fresh rather than raising."""
        raw = None
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
            except (OSError, ValueError) as exc:
                _warn(
                    f"activity ledger {self.path} is unreadable "
                    f"({type(exc).__name__}); starting fresh."
                )
        if isinstance(raw, dict) and raw.get("version") != LEDGER_VERSION:
            _warn(
                f"activity ledger {self.path} has version "
                f"{raw.get('version')!r}, expected {LEDGER_VERSION}; starting fresh."
            )

        activity = Activity.from_dict(raw)
        now = self._now()
        if activity.last_action > now + self._window_seconds:
            _warn(
                f"activity ledger {self.path} is dated in the future; "
                "starting fresh rather than idling."
            )
            activity = Activity(salt=activity.salt)
        activity.window = [
            t for t in activity.window if now - self._window_seconds < t <= now
        ]
        if not activity.salt:
            activity.salt = secrets.token_hex(8)
        self.activity = activity
        return activity

    def flush(self) -> None:
        """Atomically rewrite the ledger. A failed write leaves the old one."""
        if not self.enabled:
            return
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.activity.to_dict()))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as exc:
            _warn(f"could not write {self.path} ({exc}); pacing state not saved.")
            try:
                tmp.unlink()
            except OSError:
                pass

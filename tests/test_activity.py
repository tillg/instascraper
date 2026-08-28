"""Network-free, sleep-free tests for the activity ledger.

Every test points the ledger at `tmp_path` and injects both the clock and the
sleep, so nothing here touches the real `~/.config` and nothing blocks — the
lock-timeout test included.
"""

from __future__ import annotations

import json
import os

import pytest

from instascraper.activity import (
    LEDGER_VERSION,
    Activity,
    ActivityLedger,
    LedgerBusy,
    activity_path,
)


class FakeClock:
    """A clock the test advances explicitly; `sleep` advances it."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def make(tmp_path, clock=None, *, window_seconds: int = 3600, **kw):
    clock = clock or FakeClock()
    ledger = ActivityLedger(
        tmp_path / "activity-me.json",
        window_seconds=window_seconds,
        now=clock.now,
        sleep=clock.sleep,
        **kw,
    )
    return ledger, clock


# --- 1. schema ------------------------------------------------------------


def test_activity_round_trips_through_dict():
    before = Activity(
        salt="abc123",
        last_action=1770000000.5,
        session_requests=7,
        session_posts=2,
        day="2026-08-28",
        day_requests=41,
        day_posts=9,
        window=[1769999999.0, 1770000000.5],
    )
    assert Activity.from_dict(before.to_dict()) == before


def test_unknown_ledger_version_is_discarded_not_migrated():
    stale = Activity(session_posts=9).to_dict() | {"version": LEDGER_VERSION + 1}
    assert Activity.from_dict(stale) == Activity()


def test_missing_keys_fall_back_to_field_defaults():
    activity = Activity.from_dict({"version": LEDGER_VERSION, "session_posts": 3})
    assert (activity.session_posts, activity.day_posts, activity.window) == (3, 0, [])


@pytest.mark.parametrize("raw", [None, "not a dict", 42, {}, {"version": None}])
def test_garbage_never_raises(raw):
    assert Activity.from_dict(raw) == Activity()


def test_unparseable_field_types_yield_a_fresh_activity():
    bad = {"version": LEDGER_VERSION, "last_action": "yesterday"}
    assert Activity.from_dict(bad) == Activity()


def test_activity_path_keys_on_username_and_honors_the_override(tmp_path):
    assert activity_path("me").name == "activity-me.json"
    assert activity_path(None).name == "activity.json"          # --browser bootstrap
    assert activity_path("me", str(tmp_path / "x.json")) == tmp_path / "x.json"


# --- 2. load / prune / atomic save ---------------------------------------


def test_write_then_read_preserves_counters_and_window(tmp_path):
    clock = FakeClock()
    ledger, _ = make(tmp_path, clock)
    with ledger as l:
        l.activity.session_posts = 4
        l.activity.day_requests = 33
        l.activity.last_action = clock.t
        l.activity.window.extend([clock.t - 10, clock.t - 5])

    second, _ = make(tmp_path, clock)
    with second as l:
        assert (l.activity.session_posts, l.activity.day_requests) == (4, 33)
        assert l.activity.window == [clock.t - 10, clock.t - 5]
        assert l.activity.last_action == clock.t


def test_the_salt_is_generated_once_and_then_kept(tmp_path):
    ledger, clock = make(tmp_path)
    with ledger as l:
        salt = l.activity.salt
    assert salt
    second, _ = make(tmp_path, clock)
    with second as l:
        assert l.activity.salt == salt


def test_pruning_drops_expired_and_future_window_entries(tmp_path):
    clock = FakeClock()
    path = tmp_path / "activity-me.json"
    path.write_text(json.dumps(
        Activity(
            version=LEDGER_VERSION,
            window=[clock.t - 7200, clock.t - 10, clock.t + 60],
        ).to_dict()
    ))
    ledger, _ = make(tmp_path, clock)
    with ledger as l:
        assert l.activity.window == [clock.t - 10]


def test_a_future_dated_ledger_starts_fresh_instead_of_idling(tmp_path, capsys):
    clock = FakeClock()
    path = tmp_path / "activity-me.json"
    path.write_text(json.dumps(
        Activity(last_action=clock.t + 99999, session_posts=5).to_dict()
    ))
    ledger, _ = make(tmp_path, clock)
    with ledger as l:
        assert (l.activity.last_action, l.activity.session_posts) == (0.0, 0)
    assert "future" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    ["", "{", "not json at all", '{"version": 99, "session_posts": 5}'],
    ids=["empty", "truncated", "corrupt", "unknown-version"],
)
def test_unusable_files_degrade_to_a_fresh_ledger_with_a_warning(tmp_path, capsys, content):
    (tmp_path / "activity-me.json").write_text(content)
    ledger, _ = make(tmp_path)
    with ledger as l:
        assert l.activity.session_posts == 0
    assert "activity ledger" in capsys.readouterr().err


def test_an_unreadable_path_degrades_rather_than_raising(tmp_path, capsys):
    (tmp_path / "activity-me.json").mkdir()  # reading it raises IsADirectoryError
    ledger, _ = make(tmp_path)
    ledger._acquire()
    assert ledger.load() == Activity(salt=ledger.activity.salt)
    ledger.close()
    assert "unreadable" in capsys.readouterr().err


def test_a_missing_file_is_simply_a_fresh_ledger(tmp_path, capsys):
    ledger, _ = make(tmp_path)
    with ledger as l:
        assert l.activity.last_action == 0.0
    assert "activity ledger" not in capsys.readouterr().err


def test_the_saved_file_is_private(tmp_path):
    ledger, _ = make(tmp_path)
    with ledger:
        pass
    assert oct(os.stat(tmp_path / "activity-me.json").st_mode)[-3:] == "600"


def test_a_failed_write_leaves_the_previous_ledger_intact(tmp_path, capsys, monkeypatch):
    ledger, clock = make(tmp_path)
    with ledger as l:
        l.activity.session_posts = 3
    before = (tmp_path / "activity-me.json").read_text()

    second, _ = make(tmp_path, clock)
    with second as l:
        l.activity.session_posts = 99
        monkeypatch.setattr("os.replace", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    assert (tmp_path / "activity-me.json").read_text() == before
    assert "could not write" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.tmp"))


def test_a_disabled_ledger_never_touches_disk(tmp_path):
    ledger, _ = make(tmp_path, enabled=False)
    with ledger as l:
        l.activity.session_posts = 7
        l.activity.last_action = 1.0
    assert list(tmp_path.iterdir()) == []
    assert ledger.activity.salt == ""  # so the edge jitter falls back to the RNG


# --- 3. the run lock ------------------------------------------------------


def test_a_second_run_for_the_same_account_is_refused(tmp_path):
    clock = FakeClock()
    first, _ = make(tmp_path, clock)
    with first:
        second, _ = make(tmp_path, clock, lock_timeout=5.0)
        with pytest.raises(LedgerBusy):
            second.__enter__()
        assert clock.slept, "the retry must go through the injected sleep"
        assert sum(clock.slept) <= 5.0 + 0.1


def test_the_lock_is_released_on_exit(tmp_path):
    clock = FakeClock()
    first, _ = make(tmp_path, clock)
    with first:
        pass
    third, _ = make(tmp_path, clock)
    with third as l:  # would raise LedgerBusy if the first still held it
        assert l.activity is not None


def test_a_disabled_ledger_never_locks(tmp_path):
    clock = FakeClock()
    first, _ = make(tmp_path, clock)
    with first:
        disabled, _ = make(tmp_path, clock, enabled=False)
        with disabled:  # no lock, no wait, no file
            pass
        assert clock.slept == []

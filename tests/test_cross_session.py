"""The headline regression test: ten runs must look like one batch.

`proposal.md` measured the bug this way — ten one-URL runs versus one ten-URL
batch, same profile:

    before:  10 separate runs → 0 paced gaps,  post-counter=[1]*10
    after:   10 separate runs → 9 paced gaps,  post-counter=10

These tests drive the real `cli.main` end to end (real ledger, real Humanizer,
real `auth.get_client`) with the clock, the RNG, and the sleep injected. Only
instagrapi's `Client` and the two per-post stages are stood in for — the network
edges, not the code under test.
"""

from __future__ import annotations

import json
import random
from datetime import datetime

import pytest

import instascraper.auth as auth
import instascraper.cli as cli
from instascraper.activity import ActivityLedger
from instascraper.behavior import BehaviorProfile, Humanizer
from instascraper.models import Provenance, ScrapeResult

WALL = datetime(2026, 8, 3, 12, 0)      # inside active hours, fixed all run
SEED = 7
# Ranges are the defaults; `long_pause_prob 0` only so a "post-scale" sleep is
# unambiguously a post-scale sleep. The tail's reach into owed idle has its own
# test (`test_owed_idle_reaches_the_long_pause_tail`).
FLAGS = [
    "--username", "tillg",
    "--no-save-config",
    "--humanize-long-pause-prob", "0",
    "--humanize-warmup-calls", "2,2",   # so "warmed up once" is observable
]
POST_SCALE = BehaviorProfile().post_delay
ANDROID_UA = "Instagram 428.0.0.47.67 Android (34/14; 480dpi; Google/google; en_US)"


class Clock:
    """Fake wall clock. Sleeping advances it; every event is recorded in order."""

    def __init__(self, start: float = 1_770_000_000.0) -> None:
        self.t = start
        self.events: list[tuple[str, float]] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", seconds))
        self.t += seconds

    def mark(self, what: str) -> None:
        self.events.append((what, self.t))

    @property
    def post_scale_sleeps(self) -> list[float]:
        return [s for kind, s in self.events if kind == "sleep" and s >= POST_SCALE.lo]


class FakeIG:
    """Stands in for `instagrapi.Client` — the network edge, not our code."""

    clock: Clock
    feed_calls = 0

    def __init__(self, *a, **kw) -> None:
        self.username = "tillg"
        self.delay_range = [1, 3]
        self.request_timeout = 15

    def load_settings(self, path):
        return {"uuids": {"phone_id": "x"}}

    def get_settings(self):
        return {"uuids": {"phone_id": "x"}, "user_agent": ANDROID_UA}

    def get_timeline_feed(self):
        FakeIG.feed_calls += 1
        FakeIG.clock.mark("feed")

    def set_device(self, device=None):
        pass

    def set_user_agent(self, user_agent=""):
        pass

    def set_uuids(self, uuids):
        pass

    def dump_settings(self, path):
        pass


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Everything wired for a run: injected clock/RNG, stubbed network edges."""
    clock = Clock()
    FakeIG.clock = clock
    FakeIG.feed_calls = 0
    monkeypatch.setattr(auth, "Client", FakeIG)

    def make_humanizer(profile, ledger=None):
        return Humanizer(
            profile, rng=random.Random(SEED), sleep=clock.sleep,
            now=clock.now, wall=lambda: WALL, ledger=ledger,
        )

    def make_ledger(path, **kw):
        return ActivityLedger(path, now=clock.now, sleep=clock.sleep, **kw)

    def fake_scrape(client, shortcode, url, account, humanizer=None, **kw):
        humanizer.record("request")   # what `media_info_v1` costs, per post
        result = ScrapeResult(
            shortcode=shortcode, source_url=url, owner="someone", typename="image",
            taken_at=None, likes=1, is_video=False, caption="", comments=[],
            provenance=Provenance(
                fetched_at="now", backend="fake", account=account,
                comment_sort="likes", comment_scan_limit=200, comments_scanned=0,
            ),
        )
        return object(), result

    monkeypatch.setattr(cli, "Humanizer", make_humanizer)
    monkeypatch.setattr(cli, "ActivityLedger", make_ledger)
    monkeypatch.setattr(cli, "scrape", fake_scrape)
    monkeypatch.setattr(cli, "write_result", lambda *a, **kw: tmp_path / "out")

    session = tmp_path / "session-tillg.json"
    session.write_text("{}")
    return clock, session


def urls(n: int) -> list[str]:
    return [f"https://www.instagram.com/p/SHORT{i:04d}/" for i in range(n)]


def saved(path):
    return json.loads(path.read_text())


def ten_runs(rig, tmp_path):
    clock, session = rig
    ledger = tmp_path / "ten" / "activity-tillg.json"
    per_run = []
    for url in urls(10):
        before = len(clock.events)
        code = cli.main(
            [*FLAGS, "--session-file", str(session), "--activity-file", str(ledger), url]
        )
        assert code == cli.EXIT_OK
        per_run.append(clock.events[before:])
        clock.t += 1.0  # the next invocation starts a second later
    return ledger, per_run


def one_batch(rig, tmp_path):
    clock, session = rig
    ledger = tmp_path / "batch" / "activity-tillg.json"
    listing = tmp_path / "urls.md"
    listing.write_text("\n".join(urls(10)))
    code = cli.main(
        [*FLAGS, "--session-file", str(session), "--activity-file", str(ledger),
         "--file", str(listing)]
    )
    assert code == cli.EXIT_OK
    return ledger


def test_ten_runs_are_paced_like_one_batch(rig, tmp_path):
    clock, _ = rig
    ledger, _ = ten_runs(rig, tmp_path)
    gaps = clock.post_scale_sleeps

    # Nine, not zero (the bug) and not ten: the first run has nothing to
    # continue from, and no run pays a trailing pace after its last post.
    assert len(gaps) == 9
    assert saved(ledger)["session_posts"] == 10   # one continuous activity session
    assert saved(ledger)["day_posts"] == 10
    for gap in gaps:
        assert POST_SCALE.lo <= gap <= POST_SCALE.hi + BehaviorProfile().long_pause.hi


def test_the_batch_paces_the_same_nine_gaps(rig, tmp_path):
    clock, _ = rig
    ledger = one_batch(rig, tmp_path)
    assert len(clock.post_scale_sleeps) == 9   # between posts only — never trailing
    assert saved(ledger)["session_posts"] == 10


def test_the_request_count_differs_by_exactly_the_session_validations(rig, tmp_path):
    """The one residual: each invocation proves its session is alive."""
    batch_ledger = one_batch(rig, tmp_path)
    batch_requests = saved(batch_ledger)["day_requests"]

    clock, _ = rig
    clock.events.clear()
    ten_ledger, _ = ten_runs(rig, tmp_path)
    assert saved(ten_ledger)["day_requests"] == batch_requests + 9


def test_the_app_is_opened_once_across_ten_runs_not_ten(rig, tmp_path):
    ten_runs(rig, tmp_path)
    # 10 session validations + one warm-up of 2 calls. Ten warm-ups would be 30.
    assert FakeIG.feed_calls == 12


def test_the_owed_idle_precedes_the_first_packet_of_every_later_run(rig, tmp_path):
    """Idle paid after login is idle Instagram never observed."""
    _, per_run = ten_runs(rig, tmp_path)

    first_run = per_run[0]
    assert first_run[0][0] == "feed"  # nothing owed; it just logs in

    for events in per_run[1:]:
        kinds = [kind for kind, _ in events]
        assert kinds[0] == "sleep"
        assert events[0][1] >= POST_SCALE.lo          # a post-scale idle…
        assert kinds.index("feed") > 0                # …before the first packet

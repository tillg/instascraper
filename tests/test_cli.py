"""Network-free tests for CLI helpers (arg parsing, option resolution)."""

import random
from datetime import datetime

import instagrapi.exceptions as igex
import pytest

from instascraper.behavior import (
    PROCEED,
    STOP,
    BehaviorProfile,
    Humanizer,
    Range,
    build_profile,
)
from instascraper.cli import (
    _urls_from_file,
    build_parser,
    config_updates,
    delay_flag_notice,
    pace_between_posts,
    resolve_gate,
    resolve_options,
    with_backoff,
)


def test_urls_from_file_extracts_instagram_urls(tmp_path):
    f = tmp_path / "urls.md"
    f.write_text(
        "# My reels\n"
        "* https://www.instagram.com/reel/DXOCAyzEX8i/\n"
        "* https://www.instagram.com/reel/DZ_KsKvKAW0/\n"
        "not a url line\n"
        "https://www.instagram.com/p/ABC123/\n",
        encoding="utf-8",
    )
    assert _urls_from_file(str(f)) == [
        "https://www.instagram.com/reel/DXOCAyzEX8i/",
        "https://www.instagram.com/reel/DZ_KsKvKAW0/",
        "https://www.instagram.com/p/ABC123/",
    ]


def test_configurable_args_default_to_none():
    # Real defaults live in resolve_options, so un-passed args are None.
    a = build_parser().parse_args(["https://www.instagram.com/reel/DXOCAyzEX8i/"])
    assert a.output is None and a.comment_sort is None and a.comment_scan_limit is None
    assert a.username is None and a.password is None and a.browser is None


def test_target_dir_aliases_map_to_output():
    for flag in ("--target-dir", "--target_dir", "--output"):
        a = build_parser().parse_args([flag, "data", "u-url"])
        assert a.output == "data"


def test_parser_browser_choice_and_rejects_unknown():
    a = build_parser().parse_args(["--browser", "safari", "u-url"])
    assert a.browser == "safari"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--browser", "netscape", "u-url"])


def test_resolve_precedence_cli_over_config_over_env_over_default():
    args = build_parser().parse_args(["--username", "cli_user", "some-url"])
    cfg = {"IG_USERNAME": "cfg_user", "INSTASCRAPE_OUTPUT": "cfg_out",
           "INSTASCRAPE_DELAY": "7"}
    environ = {"IG_USERNAME": "env_user", "INSTASCRAPE_COMMENT_SCAN_LIMIT": "50"}
    opts = resolve_options(args, cfg, environ)
    assert opts["username"] == "cli_user"          # CLI wins
    assert opts["output"] == "cfg_out"             # config wins over default
    assert opts["delay"] == 7.0                    # config, cast to float
    assert opts["comment_scan_limit"] == 50        # env, cast to int
    assert opts["comment_sort"] == "likes"         # built-in default


def test_progress_start_ok_on_one_line(capsys):
    from instascraper.cli import Progress

    p = Progress()
    p.start("logging in as @tillg")
    p.ok("logged in")
    assert "logging in as @tillg… logged in\n" in capsys.readouterr().out


def test_progress_dots_between_start_and_ok(capsys):
    from instascraper.cli import Progress

    p = Progress()
    p.start("scanning up to 200 comments")
    p.tick()
    p.tick()
    p.tick()
    p.ok("3 comments")
    out = capsys.readouterr().out
    assert "scanning up to 200 comments… ..." in out   # label + inline dots
    assert "...3 comments\n" in out                    # result appended, then newline


def test_resolve_defaults_when_nothing_set():
    args = build_parser().parse_args(["some-url"])
    opts = resolve_options(args, {}, {})
    assert opts["username"] is None
    assert opts["password"] is None
    assert opts["output"] == "output"
    assert opts["delay"] == 3.0
    assert opts["comment_sort"] == "likes"
    assert opts["comment_scan_limit"] == 200
    assert opts["browser"] is None
    assert opts["session_file"] is None
    assert opts["device_profile"] == "android"
    # Humanization options stay unset so BehaviorProfile owns every default.
    assert opts["humanize"] is None
    assert all(v is None for k, v in opts.items() if k.startswith("humanize_"))


# --- humanization wiring --------------------------------------------------


def _profile(argv, cfg=None, environ=None):
    args = build_parser().parse_args(argv)
    return build_profile(resolve_options(args, cfg or {}, environ or {}))


def test_humanization_is_on_by_default():
    assert _profile(["some-url"]) == BehaviorProfile()


def test_no_humanize_disables_the_profile():
    assert _profile(["--no-humanize", "some-url"]).enabled is False


def test_no_humanize_is_never_persisted_so_humanizing_stays_the_default():
    # A one-off --no-humanize must not leave later runs unhumanized.
    opts = resolve_options(build_parser().parse_args(["--no-humanize", "some-url"]), {}, {})
    assert opts["humanize"] is False                      # honored for this run
    assert "INSTASCRAPE_HUMANIZE" not in config_updates(opts)  # but not written back


def test_other_options_are_still_persisted():
    opts = resolve_options(
        build_parser().parse_args(["--humanize-post-delay", "5,9", "some-url"]), {}, {}
    )
    updates = config_updates(opts)
    assert updates["INSTASCRAPE_HUMANIZE_POST_DELAY"] == "5,9"
    assert updates["INSTASCRAPE_OUTPUT"] == "output"


def test_humanize_flag_overrides_a_hand_written_opt_out():
    cfg = {"INSTASCRAPE_HUMANIZE": "False"}  # only ever gets here by hand-editing
    assert _profile(["some-url"], cfg=cfg).enabled is False
    assert _profile(["--humanize", "some-url"], cfg=cfg).enabled is True  # recoverable


def test_humanization_off_is_announced_every_run(capsys, monkeypatch):
    from instascraper import cli

    monkeypatch.setattr(cli, "load_config", lambda: {"INSTASCRAPE_HUMANIZE": "False"})
    monkeypatch.setattr(cli, "_urls_from_file", lambda path: [])  # stop before login
    assert cli.main(["--file", "urls.md"]) == cli.EXIT_FATAL
    assert "humanization is OFF" in capsys.readouterr().err


def test_humanize_precedence_cli_over_config_over_env_over_default():
    args = build_parser().parse_args(["--humanize-post-delay", "2,7", "some-url"])
    cfg = {"INSTASCRAPE_HUMANIZE_POST_DELAY": "5,10",
           "INSTASCRAPE_HUMANIZE_PAGE_DELAY": "4,9"}
    environ = {"INSTASCRAPE_HUMANIZE_PAGE_DELAY": "1,2",
               "INSTASCRAPE_HUMANIZE_EARLY_STOP_PROB": "0.7"}
    profile = build_profile(resolve_options(args, cfg, environ))
    assert profile.post_delay == Range(2.0, 7.0)        # CLI wins
    assert profile.page_delay == Range(4.0, 9.0)        # config beats env
    assert profile.early_stop_prob == 0.7               # env beats default
    assert profile.long_pause == BehaviorProfile().long_pause  # untouched default


def test_stale_delay_in_config_does_not_shrink_post_delay():
    # Upgrading users have INSTASCRAPE_DELAY=3 saved from before humanization.
    # It must not silently pin the flagship 20–90s idle to 3 seconds.
    profile = _profile(["some-url"], cfg={"INSTASCRAPE_DELAY": "3"})
    assert profile.post_delay == BehaviorProfile().post_delay


def test_humanize_options_round_trip_through_the_config_file(tmp_path):
    from instascraper.config import load_config, save_config

    path = tmp_path / ".env"
    args = build_parser().parse_args(
        ["--humanize-post-delay", "12,34", "--humanize-max-posts", "7", "some-url"]
    )
    save_config(config_updates(resolve_options(args, {}, {})), path=path)

    reloaded = build_profile(resolve_options(build_parser().parse_args(["some-url"]),
                                             load_config(path), {}))
    assert reloaded.post_delay == Range(12.0, 34.0)
    assert reloaded.max_posts_per_session == 7


def test_malformed_humanize_value_exits_fatally(capsys):
    from instascraper.cli import EXIT_FATAL, main

    assert main(["--humanize-post-delay", "nope", "some-url"]) == EXIT_FATAL
    assert "Invalid humanization option" in capsys.readouterr().err


def test_device_profile_defaults_to_android_and_accepts_ios():
    assert build_parser().parse_args(["some-url"]).device_profile is None
    assert resolve_options(build_parser().parse_args(["some-url"]), {}, {})[
        "device_profile"
    ] == "android"
    args = build_parser().parse_args(["--device-profile", "ios", "some-url"])
    assert resolve_options(args, {}, {})["device_profile"] == "ios"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--device-profile", "windows-phone", "some-url"])


# --- gate resolution ------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.messages = []

    def start(self, label): ...
    def ok(self, result="done"): ...
    def tick(self): ...
    def done(self): ...

    def stage(self, msg):
        self.messages.append(msg)


def _humanizer(profile, clock_start=0.0):
    """A humanizer whose injected sleep advances its own monotonic clock."""
    state = {"t": clock_start}

    def sleep(seconds):
        state["t"] += seconds

    return Humanizer(
        profile, rng=random.Random(0), sleep=sleep, now=lambda: state["t"],
        wall=lambda: datetime(2026, 8, 3, 12, 0),
    )


def test_resolve_gate_proceeds_when_clear():
    hum = _humanizer(BehaviorProfile())
    assert resolve_gate(hum, _Recorder()).action == PROCEED


def test_resolve_gate_sleeps_through_a_window_wait_then_proceeds():
    hum = _humanizer(BehaviorProfile(max_requests_per_window=2, window_seconds=600))
    hum.record("request")
    hum.record("request")
    progress = _Recorder()
    assert resolve_gate(hum, progress).action == PROCEED  # waited out the window
    assert any("rate ceiling" in m for m in progress.messages)


def test_resolve_gate_reports_stop_outside_active_hours():
    hum = Humanizer(
        BehaviorProfile(active_hours=(8, 23), active_hours_jitter=Range(0, 0)),
        rng=random.Random(0), sleep=lambda s: None,
        wall=lambda: datetime(2026, 8, 3, 4, 0),
    )
    result = resolve_gate(hum, _Recorder())
    assert result.action == STOP
    assert "outside active hours" in result.reason


def test_resolve_gate_gives_up_if_a_wait_never_clears():
    # A clock that never advances: the gate must not spin forever.
    hum = Humanizer(
        BehaviorProfile(max_requests_per_window=1, window_seconds=600),
        rng=random.Random(0), sleep=lambda s: None, now=lambda: 0.0,
        wall=lambda: datetime(2026, 8, 3, 12, 0),
    )
    hum.record("request")
    assert resolve_gate(hum, _Recorder()).action == STOP


# --- politeness backoff ---------------------------------------------------


def _flaky(failures: int):
    """An action that raises PleaseWaitFewMinutes `failures` times, then works."""
    state = {"n": 0}

    def action():
        state["n"] += 1
        if state["n"] <= failures:
            raise igex.PleaseWaitFewMinutes("Please wait a few minutes")
        return "scraped"

    action.calls = lambda: state["n"]
    return action


def test_with_backoff_returns_straight_away_on_success():
    slept = []
    hum = Humanizer(BehaviorProfile(), rng=random.Random(0), sleep=slept.append)
    assert with_backoff(hum, _Recorder(), lambda: "scraped") == "scraped"
    assert slept == []


def test_with_backoff_waits_then_retries():
    slept = []
    hum = Humanizer(
        BehaviorProfile(backoff_base=60.0, backoff_attempts=3),
        rng=random.Random(0), sleep=slept.append,
    )
    action = _flaky(2)
    progress = _Recorder()
    assert with_backoff(hum, progress, action) == "scraped"
    assert action.calls() == 3                       # two failures, then success
    assert len(slept) == 2
    assert 30.0 <= slept[0] <= 60.0                  # jittered, capped at base
    assert 60.0 <= slept[1] <= 120.0                 # doubled on the second try
    assert all("rate-limited" in m for m in progress.messages)


def test_with_backoff_reraises_once_attempts_are_spent():
    hum = Humanizer(
        BehaviorProfile(backoff_attempts=2), rng=random.Random(0), sleep=lambda s: None
    )
    with pytest.raises(igex.PleaseWaitFewMinutes):
        with_backoff(hum, _Recorder(), _flaky(99))


def test_with_backoff_fails_fast_when_humanization_is_off():
    slept = []
    hum = Humanizer(BehaviorProfile(enabled=False), rng=random.Random(0), sleep=slept.append)
    action = _flaky(1)
    with pytest.raises(igex.PleaseWaitFewMinutes):
        with_backoff(hum, _Recorder(), action)
    assert action.calls() == 1  # today's immediate-fatal behavior
    assert slept == []


# --- inter-post pacing ----------------------------------------------------


def test_humanized_runs_pace_posts_from_post_delay_not_delay():
    slept = []
    hum = Humanizer(
        BehaviorProfile(post_delay=Range(20.0, 90.0), long_pause_prob=0.0),
        rng=random.Random(0), sleep=slept.append,
    )
    waited = pace_between_posts(hum, fixed_delay=3.0)  # a stale --delay
    assert 20.0 <= waited <= 90.0                      # post_delay wins
    assert slept == [waited]


def test_no_humanize_restores_the_fixed_delay_sleep():
    slept = []
    hum = Humanizer(BehaviorProfile(enabled=False), rng=random.Random(0))
    assert pace_between_posts(hum, fixed_delay=5.0, sleep=slept.append) == 5.0
    assert slept == [5.0]


def test_no_humanize_with_zero_delay_does_not_sleep():
    slept = []
    hum = Humanizer(BehaviorProfile(enabled=False), rng=random.Random(0))
    assert pace_between_posts(hum, fixed_delay=0, sleep=slept.append) == 0.0
    assert slept == []


def test_explicit_delay_under_humanization_warns_and_is_ignored():
    args = build_parser().parse_args(["--delay", "8", "some-url"])
    profile = build_profile(resolve_options(args, {}, {}))
    notice = delay_flag_notice(profile, args.delay)
    assert notice is not None
    assert "--humanize-post-delay" in notice
    assert profile.post_delay == BehaviorProfile().post_delay  # untouched


def test_stale_delay_from_config_warns_about_nothing():
    args = build_parser().parse_args(["some-url"])
    profile = build_profile(resolve_options(args, {"INSTASCRAPE_DELAY": "3"}, {}))
    assert delay_flag_notice(profile, args.delay) is None  # silent on upgrade


def test_no_notice_when_humanization_is_off():
    args = build_parser().parse_args(["--no-humanize", "--delay", "8", "some-url"])
    profile = build_profile(resolve_options(args, {}, {}))
    assert delay_flag_notice(profile, args.delay) is None  # --delay is honored

"""Network-free tests for CLI helpers (arg parsing, option resolution)."""

import pytest

from instascraper.cli import _urls_from_file, build_parser, resolve_options


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
    assert opts == {
        "username": None, "password": None, "output": "output", "delay": 3.0,
        "comment_sort": "likes", "comment_scan_limit": 200,
        "browser": None, "session_file": None,
    }

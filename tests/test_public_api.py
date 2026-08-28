"""The published API surface, pinned.

README's "Public API" section is a promise; this is the test that keeps it true.
A rename that slips through here is a silently broken import for every consumer
pinning a tag, so the list is explicit rather than derived.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import pytest

import instascraper

PUBLIC_API = {
    "activity": [
        "Activity", "ActivityLedger", "LedgerBusy", "activity_path",
        "LEDGER_VERSION", "DEFAULT_LOCK_TIMEOUT",
    ],
    "auth": [
        "get_client", "device_family", "make_links_clickable",
        "DEFAULT_SESSION_DIR", "DELAY_RANGE", "DEVICE_PROFILES",
        "SUPPORTED_BROWSERS", "REQUEST_TIMEOUT", "IOS_DEVICE", "IOS_USER_AGENT",
    ],
    "behavior": [
        "BehaviorProfile", "Humanizer", "Range", "GateResult", "build_profile",
        "PROCEED", "WAIT", "STOP",
    ],
    "cli": [
        "main", "build_parser", "resolve_options", "config_updates", "Progress",
        "resolve_gate", "gate_before_login", "pay_owed_idle",
        "pace_between_posts", "with_backoff",
        "EXIT_OK", "EXIT_PARTIAL", "EXIT_FATAL",
    ],
    "config": ["load_config", "save_config", "ENV_KEYS", "CONFIG_DIR", "CONFIG_PATH"],
    "fingerprint": ["Client", "FORGED_HEADERS", "NAV_CHAIN", "CDN_HEADERS"],
    "models": ["ScrapeResult", "Comment", "Provenance"],
    "scraper": ["scrape", "select_top_comments", "NullProgress"],
    "url": ["parse_shortcode"],
    "writer": [
        "write_result", "render_markdown", "render_metadata",
        "MEDIA_EXTS", "IMAGE_EXTS", "VIDEO_EXTS",
    ],
}

HUMANIZER_METHODS = [
    "delay", "sample_delay", "gate", "record", "owed_idle", "is_new_session",
    "is_cold_open", "should_stop_early", "clamp_scan_limit", "can_backoff",
    "backoff", "wait", "warmup", "pacing_summary",
]

LEDGER_METHODS = ["load", "flush", "close"]


@pytest.mark.parametrize("module,names", sorted(PUBLIC_API.items()))
def test_documented_symbols_exist(module, names):
    mod = importlib.import_module(f"instascraper.{module}")
    missing = [n for n in names if not hasattr(mod, n)]
    assert not missing, f"instascraper.{module} lost: {missing}"


def test_humanizer_keeps_its_documented_methods():
    from instascraper.behavior import Humanizer

    missing = [m for m in HUMANIZER_METHODS if not callable(getattr(Humanizer, m, None))]
    assert not missing, f"Humanizer lost: {missing}"


def test_activity_ledger_keeps_its_documented_methods():
    from instascraper.activity import ActivityLedger

    missing = [m for m in LEDGER_METHODS if not callable(getattr(ActivityLedger, m, None))]
    assert not missing, f"ActivityLedger lost: {missing}"


def test_the_console_script_entry_point_resolves():
    """`instascrape = instascraper.cli:main` must keep pointing at something."""
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    target = pyproject["project"]["scripts"]["instascrape"]
    module_path, _, func = target.partition(":")
    assert callable(getattr(importlib.import_module(module_path), func))


def test_the_version_is_declared_once():
    """`__version__` and pyproject must not drift apart."""
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert instascraper.__version__ == pyproject["project"]["version"]


def test_the_declared_python_floor_is_met_by_the_interpreter_running_the_tests():
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    floor = tuple(int(p) for p in pyproject["project"]["requires-python"].lstrip(">=").split("."))
    assert sys.version_info[: len(floor)] >= floor

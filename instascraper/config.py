"""Persist credentials and option defaults to a private `.env`.

Stored at ~/.config/instascraper/.env (chmod 600, never committed). Lets the
user pass --username/--password/--target-dir etc. once and omit them next time.
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "instascraper"
CONFIG_PATH = CONFIG_DIR / ".env"

# CLI option dest -> .env key
ENV_KEYS = {
    "username": "IG_USERNAME",
    "password": "IG_PASSWORD",
    "output": "INSTASCRAPE_OUTPUT",
    "delay": "INSTASCRAPE_DELAY",
    "comment_sort": "INSTASCRAPE_COMMENT_SORT",
    "comment_scan_limit": "INSTASCRAPE_COMMENT_SCAN_LIMIT",
    "browser": "INSTASCRAPE_BROWSER",
    "session_file": "INSTASCRAPE_SESSION_FILE",
    "device_profile": "INSTASCRAPE_DEVICE_PROFILE",
    # Humanization (instascraper.behavior). Grouped under one prefix so the
    # .env stays readable; every BehaviorProfile field is reachable from here.
    "humanize": "INSTASCRAPE_HUMANIZE",
    "humanize_request_delay": "INSTASCRAPE_HUMANIZE_REQUEST_DELAY",
    "humanize_page_delay": "INSTASCRAPE_HUMANIZE_PAGE_DELAY",
    "humanize_post_delay": "INSTASCRAPE_HUMANIZE_POST_DELAY",
    "humanize_long_pause": "INSTASCRAPE_HUMANIZE_LONG_PAUSE",
    "humanize_long_pause_prob": "INSTASCRAPE_HUMANIZE_LONG_PAUSE_PROB",
    "humanize_early_stop_prob": "INSTASCRAPE_HUMANIZE_EARLY_STOP_PROB",
    "humanize_warmup_calls": "INSTASCRAPE_HUMANIZE_WARMUP_CALLS",
    "humanize_scan_depth": "INSTASCRAPE_HUMANIZE_SCAN_DEPTH",
    "humanize_max_requests": "INSTASCRAPE_HUMANIZE_MAX_REQUESTS",
    "humanize_max_posts": "INSTASCRAPE_HUMANIZE_MAX_POSTS",
    "humanize_window_seconds": "INSTASCRAPE_HUMANIZE_WINDOW_SECONDS",
    "humanize_max_requests_per_window": "INSTASCRAPE_HUMANIZE_MAX_REQUESTS_PER_WINDOW",
    "humanize_active_hours": "INSTASCRAPE_HUMANIZE_ACTIVE_HOURS",
    "humanize_active_hours_jitter": "INSTASCRAPE_HUMANIZE_ACTIVE_HOURS_JITTER",
    "humanize_backoff_base": "INSTASCRAPE_HUMANIZE_BACKOFF_BASE",
    "humanize_backoff_max": "INSTASCRAPE_HUMANIZE_BACKOFF_MAX",
    "humanize_backoff_attempts": "INSTASCRAPE_HUMANIZE_BACKOFF_ATTEMPTS",
    "humanize_seed": "INSTASCRAPE_HUMANIZE_SEED",
}


def load_config(path: Path = CONFIG_PATH) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (blank lines and #comments ignored)."""
    cfg: dict[str, str] = {}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip()
    return cfg


def save_config(updates: dict[str, str], path: Path = CONFIG_PATH) -> None:
    """Merge `updates` into the stored config and write it back (chmod 600)."""
    cfg = load_config(path)
    for key, value in updates.items():
        if value is not None and value != "":
            cfg[key] = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# instascraper config — credentials + option defaults.",
        "# Auto-written by `instascrape`. chmod 600; never commit.",
    ]
    lines += [f"{k}={v}" for k, v in cfg.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

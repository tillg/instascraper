"""Persist credentials and option defaults to a private `.env`.

Stored at ~/.config/insta_scraper/.env (chmod 600, never committed). Lets the
user pass --username/--password/--target-dir etc. once and omit them next time.
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "insta_scraper"
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
        "# insta_scraper config — credentials + option defaults.",
        "# Auto-written by `instascrape`. chmod 600; never commit.",
    ]
    lines += [f"{k}={v}" for k, v in cfg.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

"""Authentication: durable instagrapi session with password login.

Primary path (Option B): a real `Client.login(username, password)` registers a
stable mobile device; we persist the full session with `dump_settings` and reuse
it via `load_settings` on later runs — logging in again only if the session
died, and then **keeping the same device UUIDs** so Instagram doesn't flag a
"new device" every run. 2FA and security challenges are handled interactively.

`--browser` remains as an optional one-shot bootstrap (imports a logged-in
browser session) but is less durable than password login.
"""

from __future__ import annotations

import getpass
import os
import re
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

from instascraper.scraper import NullProgress

DEFAULT_SESSION_DIR = Path.home() / ".config" / "instascraper"
INSTAGRAM_BASE = "https://www.instagram.com"
SUPPORTED_BROWSERS = ("safari", "chrome", "brave", "edge", "firefox", "chromium", "opera", "vivaldi")

# Client-app-like pacing: instagrapi sleeps a random N seconds between private
# API calls, so a single post's requests don't look like a burst.
DELAY_RANGE = [1, 3]

# Per-request timeout (seconds). instagrapi defaults to 1s, which is far too
# short for CDN media downloads — they time out and the post fails.
REQUEST_TIMEOUT = 15


def make_links_clickable(message: str) -> str:
    """Turn relative Instagram URLs in a message into absolute, clickable ones."""
    return re.sub(r"(?<=\s)(/[^\s]+)", lambda m: INSTAGRAM_BASE + m.group(1), str(message))


def _challenge_code_handler(username: str, choice) -> str:
    """Prompt for the verification code Instagram sends during a challenge."""
    via = getattr(choice, "name", str(choice))
    return input(
        f"Instagram sent a verification code to your {via} for @{username}. Enter it: "
    ).strip()


def _build_client() -> Client:
    client = Client()
    client.delay_range = DELAY_RANGE
    client.request_timeout = REQUEST_TIMEOUT
    client.challenge_code_handler = _challenge_code_handler
    return client


def _settings_path(username: str | None, session_file: str | None) -> Path:
    if session_file:
        return Path(session_file)
    name = f"session-{username}.json" if username else "session.json"
    return DEFAULT_SESSION_DIR / name


def _dump(client: Client, spath: Path) -> None:
    spath.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(str(spath))
    try:
        os.chmod(spath, 0o600)
    except OSError:
        pass


def _password_login(client: Client, username: str, password: str) -> None:
    """Password login, prompting for a 2FA code if required."""
    try:
        client.login(username, password)
    except TwoFactorRequired:
        code = input(f"Enter the 2FA code for @{username}: ").strip()
        client.login(username, password, verification_code=code)


def _sessionid_from_browser(browser: str) -> str:
    """Read the instagram.com `sessionid` cookie from a logged-in browser."""
    browser = browser.lower()
    if browser not in SUPPORTED_BROWSERS:
        raise SystemExit(
            f"Unsupported browser {browser!r}. Choose one of: {', '.join(SUPPORTED_BROWSERS)}."
        )
    try:
        import browser_cookie3
    except ImportError as exc:
        raise SystemExit(
            "browser_cookie3 is required for --browser. Install it: pip install browser_cookie3"
        ) from exc

    reader = getattr(browser_cookie3, browser, None)
    if reader is None:
        raise SystemExit(f"browser_cookie3 has no reader for {browser!r}.")

    try:
        jar = reader(domain_name="instagram.com")
    except PermissionError as exc:
        raise SystemExit(
            f"Permission denied reading {browser} cookies ({exc}).\n"
            "macOS protects browser cookie stores. Either:\n"
            "  • Grant Full Disk Access to your terminal app: System Settings → "
            "Privacy & Security → Full Disk Access → enable it, then fully quit "
            "and reopen the terminal and retry; or\n"
            "  • Use password login instead (no --browser)."
        ) from exc
    except Exception as exc:
        raise SystemExit(
            f"Could not read {browser} cookies: {exc}\n"
            "Make sure you are logged into instagram.com in that browser."
        ) from exc

    for c in jar:
        if getattr(c, "name", "") == "sessionid" and c.value:
            return c.value
    raise SystemExit(
        f"No active Instagram login found in {browser}. "
        f"Log into {INSTAGRAM_BASE} in {browser} first, then retry."
    )


def get_client(
    session_file: str | None = None,
    browser: str | None = None,
    username: str | None = None,
    password: str | None = None,
    progress=None,
) -> tuple[Client, str]:
    """Return a logged-in instagrapi Client and the account name.

    1. Reuse a persisted session (no login call — works in non-interactive runs).
    2. `--browser`: one-shot bootstrap from a logged-in browser session.
    3. Password login (durable); persists the session for future reuse.
    """
    progress = progress or NullProgress()
    username = username or os.environ.get("IG_USERNAME")
    spath = _settings_path(username, session_file)
    client = _build_client()
    saved_uuids = None

    # 1. Reuse a persisted session, validated cheaply. No password needed.
    if spath.exists():
        try:
            client.load_settings(str(spath))
            client.request_timeout = REQUEST_TIMEOUT  # load_settings resets it to 1s
            client.get_timeline_feed()  # raises if the session is dead
            return client, client.username or username or "unknown"
        except Exception:
            try:
                saved_uuids = client.get_settings().get("uuids") or None
            except Exception:
                saved_uuids = None
            client = _build_client()  # reset; re-auth below (keeping uuids)

    # 2. Optional browser bootstrap.
    if browser:
        sessionid = _sessionid_from_browser(browser)
        try:
            client.login_by_sessionid(sessionid)
        except Exception as exc:
            raise SystemExit(
                f"Imported the {browser} session, but Instagram rejected it "
                f"({type(exc).__name__}: {str(exc)[:80]}).\n"
                "The browser session may be flagged. Use password login instead "
                "(run without --browser)."
            ) from exc
        _dump(client, spath)
        return client, client.username

    # 3. Durable password login (keeps device UUIDs stable across relogins).
    if not username:
        raise SystemExit(
            "No Instagram username. Set IG_USERNAME or pass --username "
            "(or use --browser to import a logged-in browser session)."
        )
    if saved_uuids:
        client.set_uuids(saved_uuids)
    password = password or os.environ.get("IG_PASSWORD")
    if not password:
        progress.done()  # close the "logging in…" line before prompting
        password = getpass.getpass(f"Enter Instagram password for @{username}: ")
    try:
        _password_login(client, username, password)
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"Login failed: {make_links_clickable(exc)}") from exc

    _dump(client, spath)
    return client, client.username

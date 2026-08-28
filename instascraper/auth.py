"""Authentication: durable session with password login and a stable device.

The client is `fingerprint.Client`, not `instagrapi.Client` — same API, but its
headers and CDN fetches don't identify the library. Device identity is this
module's business (minted once, then never touched); everything else about how a
request looks is `fingerprint.py`'s.

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

from instagrapi.exceptions import TwoFactorRequired

from instascraper.fingerprint import Client
from instascraper.scraper import NullProgress

DEFAULT_SESSION_DIR = Path.home() / ".config" / "instascraper"
INSTAGRAM_BASE = "https://www.instagram.com"
SUPPORTED_BROWSERS = ("safari", "chrome", "brave", "edge", "firefox", "chromium", "opera", "vivaldi")

# Client-app-like pacing: instagrapi sleeps a random N seconds between private
# API calls, so a single post's requests don't look like a burst. Used only when
# humanization is off; otherwise it comes from `BehaviorProfile.request_delay`.
DELAY_RANGE = [1, 3]

# Per-request timeout (seconds). instagrapi defaults to 1s, which is far too
# short for CDN media downloads — they time out and the post fails.
REQUEST_TIMEOUT = 15

DEVICE_PROFILES = ("android", "ios")

# An iPhone-flavoured device + user-agent. See `_apply_device` for why this is
# not the default: instagrapi's request envelope stays Android regardless.
IOS_DEVICE = {
    "manufacturer": "Apple",
    "model": "iPhone15,3",
    "device": "iPhone",
    "cpu": "arm64",
    "dpi": "460dpi",
    "resolution": "1290x2796",
}
IOS_USER_AGENT = (
    "Instagram 208.0.0.32.135 (iPhone15,3; iOS 17_5_1; en_US; en-US; "
    "scale=3.00; 1290x2796; 314665256) AppleWebKit/605.1.15"
)


def make_links_clickable(message: str) -> str:
    """Turn relative Instagram URLs in a message into absolute, clickable ones."""
    return re.sub(r"(?<=\s)(/[^\s]+)", lambda m: INSTAGRAM_BASE + m.group(1), str(message))


def _challenge_code_handler(username: str, choice) -> str:
    """Prompt for the verification code Instagram sends during a challenge."""
    via = getattr(choice, "name", str(choice))
    return input(
        f"Instagram sent a verification code to your {via} for @{username}. Enter it: "
    ).strip()


def _build_client(delay_range: list | None = None) -> Client:
    client = Client()
    client.delay_range = list(delay_range or DELAY_RANGE)
    client.request_timeout = REQUEST_TIMEOUT
    client.challenge_code_handler = _challenge_code_handler
    return client


def _delay_range(humanizer) -> list:
    """Per-request pacing, single-sourced from the behavior profile.

    instagrapi already sleeps `delay_range` before each private call, so we set
    it from the profile rather than wrapping a second sleep around every call.
    """
    if humanizer is None or not humanizer.profile.enabled:
        return DELAY_RANGE
    band = humanizer.profile.request_delay
    return [band.lo, band.hi]


def device_family(settings: dict) -> str:
    """Which device family a persisted session was fingerprinted as."""
    agent = settings.get("user_agent") or ""
    return "ios" if ("iOS" in agent or "iPhone" in agent) else "android"


def _apply_device(client: Client, device_profile: str, progress) -> None:
    """Seed the emulated device. Only ever called when minting a NEW session.

    Changing the device of a live session is itself a new-device event — the
    exact harm this is meant to avoid — so a reused session keeps whatever it
    already has (see `get_client`).
    """
    if device_profile == "ios":
        # Honest warning: instagrapi speaks the Android private API. It sends
        # X-IG-Android-ID and X-IG-Capabilities: 3brTv10= on every request
        # (instagrapi/mixins/private.py), so an iPhone user-agent contradicts
        # the rest of the envelope. "android" is the coherent choice.
        progress.stage(
            "note: --device-profile ios changes the user-agent only — instagrapi's "
            "request envelope (X-IG-Android-ID, X-IG-Capabilities) stays Android, "
            "so the fingerprint is mixed. 'android' is the coherent choice."
        )
        client.set_device(IOS_DEVICE)
        client.set_user_agent(IOS_USER_AGENT)  # after set_device, which rebuilds the UA
    else:
        client.set_device()  # instagrapi's coherent Android default


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
    humanizer=None,
    device_profile: str = "android",
) -> tuple[Client, str]:
    """Return a logged-in instagrapi Client and the account name.

    1. Reuse a persisted session (no login call — works in non-interactive runs).
    2. `--browser`: one-shot bootstrap from a logged-in browser session.
    3. Password login (durable); persists the session for future reuse.

    A fresh `Client.login` is a flag-risk event, so it only ever happens when
    there is no usable session — never speculatively. `device_profile` seeds the
    emulated device when minting a new session and is deliberately *not* applied
    to a reused one. `humanizer`, when given, supplies per-request pacing and an
    optional app-open warm-up.
    """
    progress = progress or NullProgress()
    username = username or os.environ.get("IG_USERNAME")
    spath = _settings_path(username, session_file)
    delay_range = _delay_range(humanizer)
    client = _build_client(delay_range)
    saved_uuids = None

    # 1. Reuse a persisted session, validated cheaply. No password needed.
    if spath.exists():
        try:
            client.load_settings(str(spath))
            client.request_timeout = REQUEST_TIMEOUT  # load_settings resets it to 1s
            client.delay_range = list(delay_range)    # …and the pacing with it
            # The session is authoritative about the device: re-fingerprinting a
            # live session is itself the new-device event we're avoiding.
            existing = device_family(client.get_settings())
            if existing != device_profile:
                progress.stage(
                    f"session uses {existing!r}; config requests {device_profile!r}; "
                    f"keeping the existing session. To switch, delete {spath} and "
                    "log in again (expect a one-time new-device prompt — confirm "
                    "it was you)."
                )
            client.get_timeline_feed()  # raises if the session is dead
            if humanizer is not None:
                # Not a free health check: this is the run's *first request*, and
                # an observer cannot tell it from a fetch. So it is counted like
                # any other — otherwise no ceiling can ever see it.
                humanizer.record("request")
                # Warm up only on a cold open. Ten invocations in three minutes
                # would otherwise be ten "just opened the app" bursts, and the
                # repetition is itself the signal.
                if humanizer.is_cold_open():
                    humanizer.warmup(client)
            return client, client.username or username or "unknown"
        except Exception:
            try:
                saved_uuids = client.get_settings().get("uuids") or None
            except Exception:
                saved_uuids = None
            client = _build_client(delay_range)  # reset; re-auth below (keeping uuids)

    # 2. Optional browser bootstrap.
    if browser:
        sessionid = _sessionid_from_browser(browser)
        _apply_device(client, device_profile, progress)
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
        if humanizer is not None:
            humanizer.warmup(client)  # minting a session is an app-open, gap or not
        return client, client.username

    # 3. Durable password login (keeps device UUIDs stable across relogins).
    if not username:
        raise SystemExit(
            "No Instagram username. Set IG_USERNAME or pass --username "
            "(or use --browser to import a logged-in browser session)."
        )
    # UUIDs first: `set_device` seeds its app-version pick from settings["uuids"],
    # so restoring them keeps the app version stable across re-logins too.
    if saved_uuids:
        client.set_uuids(saved_uuids)
    _apply_device(client, device_profile, progress)
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
    if humanizer is not None:
        # Unconditional, unlike the reused-session path: a fresh login only
        # happens when there is no usable session, so it cannot repeat on a tight
        # loop — and a login with no app-open around it is a louder signal than
        # the burst the gating removes.
        humanizer.warmup(client)
    return client, client.username

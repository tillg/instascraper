"""Command-line entrypoint: URL(s) -> <target-dir>/<shortcode>/ folders.

Options resolve in this order: explicit CLI flag > saved config (.env) >
environment variable > built-in default. After a successful login the resolved
options are saved back to ~/.config/instascraper/.env so they can be omitted
next time.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import instagrapi.exceptions as igex

from instascraper.auth import DELAY_RANGE, get_client
from instascraper.behavior import (
    STOP,
    WAIT,
    GateResult,
    Humanizer,
    build_profile,
)
from instascraper.config import CONFIG_PATH, ENV_KEYS, load_config, save_config
from instascraper.scraper import scrape
from instascraper.url import parse_shortcode
from instascraper.writer import write_result

_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+")

# Exit codes
EXIT_OK = 0
EXIT_PARTIAL = 1  # some items skipped
EXIT_FATAL = 2    # auth / rate-limit — stopped early

_BROWSERS = ["safari", "chrome", "brave", "edge", "firefox", "chromium", "opera", "vivaldi"]


class Progress:
    """Live terminal progress in an 'announce → complete on the same line'
    style: `start(label)` prints `label… ` (no newline), then `ok(result)`
    finishes the line. `tick()` appends an inline dot during long steps."""

    def __init__(self) -> None:
        self._open = False  # an unfinished line is in progress

    def done(self) -> None:
        if self._open:
            print(flush=True)
            self._open = False

    def start(self, label: str) -> None:
        self.done()
        print(f"    {label}… ", end="", flush=True)
        self._open = True

    def ok(self, result: str = "done") -> None:
        if self._open:
            print(result, flush=True)
            self._open = False
        else:
            print(f"    {result}", flush=True)

    def tick(self) -> None:
        print(".", end="", flush=True)
        self._open = True

    def stage(self, msg: str) -> None:
        self.done()
        print(f"    {msg}", flush=True)


def _urls_from_file(path: str) -> list[str]:
    return _URL_RE.findall(Path(path).read_text(encoding="utf-8"))


_DESCRIPTION = """\
Archive Instagram posts and reels to local folders — media, caption, and the
top 10 comments — for personal, offline keeping.

Give it a single post/reel URL or a file of URLs. It logs in as you once,
persists the session (so later runs need no password), downloads all media
(images, videos, every carousel item), ranks the comments, and writes a
readable post.md plus a metadata.json per post."""

_EPILOG = f"""\
examples:
  # First run — log in once; credentials are saved for next time:
  instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/" --username tillg --password 'PW'

  # Later runs — username/password/options come from saved config:
  instascrape "https://www.instagram.com/reel/DZ_KsKvKAW0/"

  # Batch a file of URLs into data/, idling 30–120s between posts:
  instascrape --file SAMPLE_URLS.md --target-dir data --humanize-post-delay 30,120

  # Bootstrap auth from a logged-in browser instead of typing a password:
  instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/" --browser safari

  # Exact top-by-likes (scan every comment — slower, and a loud bot signal):
  instascrape "<url>" --comment-scan-limit 0 --no-humanize

output (one folder per post):
  <target-dir>/<shortcode>/
    post.md        caption + embedded media + top 10 comments + provenance header
    metadata.json  raw fields + provenance (machine-readable)
    <media files>  <shortcode>.mp4/.jpg; carousels get _1, _2, ... in order

auth & config:
  - First login needs --username/--password (or --browser). The session is then
    persisted to ~/.config/instascraper/session-<user>.json and reused, with a
    stable device id, so you are not re-logging-in (and tripping Instagram) each run.
  - 2FA / security-challenge codes (email or SMS) are prompted for when needed.
  - Options & credentials are saved to {CONFIG_PATH} (chmod 600) unless
    --no-save-config; precedence is: CLI flag > saved config > env var > default.

comment ranking:
  "likes" (default) = the 10 comments with the highest like_count among the ones
  actually scanned. This is a constructed ranking, NOT Instagram's in-app "top
  comments" order (which is not exposed). "instagram" = first returned
  (latest-first). The rule, and how many comments were really paged, are
  recorded in every post.md.

humanization (on by default):
  Runs are paced like a person using the app — sampled think-time between
  requests, pages, and posts; a human-scale comment depth (so
  --comment-scan-limit 0 becomes ~200, not "page every comment"); per-session
  and per-hour rate ceilings; an 08:00–23:00 active-hours window; and a polite
  wait-and-retry after a rate-limit signal. Outside active hours or past a
  session ceiling the run ends gracefully (exit 1) rather than blocking for
  hours. Every parameter is a --humanize-* flag; --no-humanize restores the old
  fast behavior. This lowers, but cannot eliminate, the chance of being flagged.

exit codes:
  0 = all good   1 = some items skipped (not found/private/transient)   2 = fatal
  (auth failed / rate-limited — stopped early)

note: Personal-use tool. Automated collection is against Instagram's Terms of
Service, and exports contain other people's personal data — keep them private,
don't republish."""

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="instascrape",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=_DESCRIPTION,
        epilog=_EPILOG,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "url", nargs="?",
        help="A single Instagram post/reel/tv URL (e.g. .../reel/DXOCAyzEX8i/)",
    )
    src.add_argument(
        "--file", metavar="PATH",
        help="A text/markdown file to scrape every Instagram URL from (batch mode)",
    )

    # Configurable options default to None so we can tell when the user set
    # them (real defaults are applied in resolve_options).
    p.add_argument(
        "--username", metavar="NAME",
        help="Instagram account to log in as. Saved to config; default: stored value or $IG_USERNAME",
    )
    p.add_argument(
        "--password", metavar="PW",
        help="Instagram password. Only needed for the FIRST login; saved to config afterwards",
    )
    p.add_argument(
        "--target-dir", "--target_dir", "--output", dest="output", metavar="DIR",
        help="Where to write the per-post folders. Default: output  (alias: --output)",
    )
    p.add_argument(
        "--session-file", metavar="PATH",
        help="Override the saved-session location (instagrapi settings JSON)",
    )
    p.add_argument(
        "--browser", choices=_BROWSERS, metavar="BROWSER",
        help="Bootstrap login by importing a logged-in browser session instead of a "
        "password. One of: " + ", ".join(_BROWSERS),
    )
    p.add_argument(
        "--device-profile", choices=["android", "ios"], metavar="FAMILY",
        help="Device family to emulate when creating a NEW session: android "
        "(default, coherent) or ios (user-agent only — instagrapi's request "
        "envelope stays Android). An existing session keeps its own device.",
    )
    p.add_argument(
        "--delay", type=float, metavar="SECONDS",
        help="Pause between posts in batch mode. Only used with --no-humanize; "
        "humanized runs pace posts with --humanize-post-delay. Default: 3",
    )
    p.add_argument(
        "--comment-sort", choices=["likes", "instagram"], metavar="MODE",
        help="How to pick the top 10 comments: 'likes' (default, by like_count) or "
        "'instagram' (first returned, latest-first)",
    )
    p.add_argument(
        "--comment-scan-limit", type=int, metavar="N",
        help="How many comments to scan before ranking by likes. Default: 200. "
        "0 = scan all — but under humanization that is clamped to a human-scale "
        "depth (--humanize-scan-depth); pair it with --no-humanize to really "
        "scan everything (exact, but slower and a loud bot signal)",
    )
    p.add_argument(
        "--no-save-config", action="store_true",
        help="Do not write the resolved options/credentials to the config file",
    )

    h = p.add_argument_group(
        "humanization",
        "Pace the run like a person using the app: sampled think-time, human-scale "
        "comment depth, rate ceilings, active hours. On by default. Ranges are "
        '"LO,HI"; every default lives in instascraper.behavior.BehaviorProfile.',
    )
    h.add_argument(
        "--no-humanize", dest="humanize", action="store_false", default=None,
        help="Turn humanization off FOR THIS RUN: fixed --delay between posts, "
        "--comment-scan-limit 0 really means all. Faster, easier to fingerprint. "
        "Deliberately not saved to config — humanization stays the default.",
    )
    h.add_argument(
        "--humanize", dest="humanize", action="store_true", default=None,
        help="Force humanization on. It already is by default; use this to "
        "override an INSTASCRAPE_HUMANIZE=false set by hand in the config/env.",
    )
    h.add_argument(
        "--humanize-request-delay", metavar="LO,HI",
        help="Seconds between private API calls. Default: 1,4",
    )
    h.add_argument(
        "--humanize-page-delay", metavar="LO,HI",
        help="Seconds between comment pages. Default: 2,8",
    )
    h.add_argument(
        "--humanize-post-delay", metavar="LO,HI",
        help="Seconds between posts in batch mode — the dominant idle. Default: 20,90",
    )
    h.add_argument(
        "--humanize-long-pause", metavar="LO,HI",
        help="Seconds for the occasional 'got distracted' gap. Default: 30,120",
    )
    h.add_argument(
        "--humanize-long-pause-prob", metavar="P",
        help="Chance a pause becomes a long one, 0–1. Default: 0.2",
    )
    h.add_argument(
        "--humanize-early-stop-prob", metavar="P",
        help="Per-page chance of stopping comment paging early, 0–1. Default: 0.3",
    )
    h.add_argument(
        "--humanize-warmup-calls", metavar="LO,HI",
        help="App-open calls made at session start. Default: 0,2",
    )
    h.add_argument(
        "--humanize-scan-depth", metavar="N",
        help="What --comment-scan-limit 0 becomes under humanization. Default: 200",
    )
    h.add_argument(
        "--humanize-max-requests", metavar="N",
        help="Request ceiling for one session. Default: 300",
    )
    h.add_argument(
        "--humanize-max-posts", metavar="N",
        help="Post ceiling for one session. Default: 60",
    )
    h.add_argument(
        "--humanize-window-seconds", metavar="N",
        help="Length of the rolling rate window, in seconds. Default: 3600",
    )
    h.add_argument(
        "--humanize-max-requests-per-window", metavar="N",
        help="Request ceiling within the rolling window. Default: 200",
    )
    h.add_argument(
        "--humanize-active-hours", metavar="START,END",
        help='Local hours when activity is plausible, or "off" for anytime. '
        "Outside them the run stops gracefully. Default: 8,23",
    )
    h.add_argument(
        "--humanize-active-hours-jitter", metavar="LO,HI",
        help="Minutes of jitter on the active-hours edges. Default: 0,30",
    )
    h.add_argument(
        "--humanize-backoff-base", metavar="SECONDS",
        help="First wait after a rate-limit signal; doubles per attempt. Default: 60",
    )
    h.add_argument(
        "--humanize-backoff-max", metavar="SECONDS",
        help="Cap on a single backoff wait. Default: 900",
    )
    h.add_argument(
        "--humanize-backoff-attempts", metavar="N",
        help="How many times to wait-and-retry a rate-limited post. Default: 3",
    )
    h.add_argument(
        "--humanize-seed", metavar="N",
        help="Seed the pacing RNG for reproducible runs. Default: random",
    )
    return p


def _pick(cli_val, env_key, cfg, environ, default, cast=str):
    if cli_val is not None:
        return cli_val
    if env_key in cfg:
        return cast(cfg[env_key])
    if environ.get(env_key):
        return cast(environ[env_key])
    return default


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# Humanization options are left as raw strings and default to None: unset means
# "use the BehaviorProfile default", which keeps one source of default truth and
# keeps unset values out of the saved .env.
_HUMANIZE_KEYS = [k for k in ENV_KEYS if k.startswith("humanize_")]


def resolve_options(args, cfg, environ=None) -> dict:
    """Merge CLI args, saved config, and env into effective options."""
    environ = environ if environ is not None else os.environ
    opts = {
        "username": _pick(args.username, "IG_USERNAME", cfg, environ, None),
        "password": _pick(args.password, "IG_PASSWORD", cfg, environ, None),
        "output": _pick(args.output, "INSTASCRAPE_OUTPUT", cfg, environ, "output"),
        "delay": _pick(args.delay, "INSTASCRAPE_DELAY", cfg, environ, 3.0, float),
        "comment_sort": _pick(args.comment_sort, "INSTASCRAPE_COMMENT_SORT", cfg, environ, "likes"),
        "comment_scan_limit": _pick(
            args.comment_scan_limit, "INSTASCRAPE_COMMENT_SCAN_LIMIT", cfg, environ, 200, int
        ),
        "browser": _pick(args.browser, "INSTASCRAPE_BROWSER", cfg, environ, None),
        "session_file": _pick(args.session_file, "INSTASCRAPE_SESSION_FILE", cfg, environ, None),
        "device_profile": _pick(
            args.device_profile, "INSTASCRAPE_DEVICE_PROFILE", cfg, environ, "android"
        ),
        "humanize": _pick(args.humanize, "INSTASCRAPE_HUMANIZE", cfg, environ, None, _as_bool),
    }
    for key in _HUMANIZE_KEYS:
        opts[key] = _pick(getattr(args, key, None), ENV_KEYS[key], cfg, environ, None)
    return opts


# How many times a WAIT verdict is slept through before giving up on the batch.
_MAX_GATE_WAITS = 3


def resolve_gate(humanizer, progress, kind: str = "post") -> GateResult:
    """Sleep through bounded WAITs and return the settled PROCEED/STOP verdict."""
    for _ in range(_MAX_GATE_WAITS):
        result = humanizer.gate(kind)
        if result.action != WAIT:
            return result
        progress.stage(f"rate ceiling ({result.reason}) — pausing {result.seconds:.0f}s")
        humanizer.wait(result.seconds)
    return GateResult(STOP, reason="rate ceiling did not clear")


def with_backoff(humanizer, progress, action):
    """Run `action()`, waiting out `PleaseWaitFewMinutes` the way a human would.

    Re-raises once the humanizer's backoff attempts are spent — and immediately
    when humanization is off, which is today's fail-fast behavior.
    """
    attempt = 0
    while True:
        try:
            return action()
        except igex.PleaseWaitFewMinutes as exc:
            if not humanizer.can_backoff(attempt):
                raise
            progress.done()
            progress.stage(
                f"rate-limited ({exc}) — waiting, attempt "
                f"{attempt + 1}/{humanizer.profile.backoff_attempts}"
            )
            humanizer.backoff(attempt)
            attempt += 1


def pace_between_posts(humanizer, fixed_delay, sleep=time.sleep) -> float:
    """Idle between two posts; returns the seconds waited.

    Under humanization this is the sampled `post_delay` — the dominant idle, and
    the one signal the old fixed `--delay` got most wrong. `--delay` only ever
    drives the unhumanized path.
    """
    if humanizer.profile.enabled:
        return humanizer.delay("post")
    if fixed_delay:
        sleep(fixed_delay)
        return fixed_delay
    return 0.0


# Options that are never written back to the config file. `humanize` is here on
# purpose: humanization is the default and must *stay* the default. Persisting a
# one-off `--no-humanize` would silently leave every later run unhumanized —
# precisely the state this whole change exists to avoid. Opting out is per-run;
# a permanent opt-out has to be a deliberate hand-edit of the .env.
_NEVER_SAVED = frozenset({"humanize"})


def config_updates(opts: dict) -> dict:
    """The `.env` writes for a successful run: set options, minus `_NEVER_SAVED`."""
    return {
        ENV_KEYS[k]: opts[k]
        for k in ENV_KEYS
        if k not in _NEVER_SAVED and opts.get(k) is not None
    }


def delay_flag_notice(profile, explicit_delay) -> str | None:
    """The one-time `--delay` deprecation notice, or None.

    A `delay` left in `.env` by a pre-humanization run is ignored silently, so
    upgrading never springs a 3s idle on anyone; an explicitly passed flag is
    worth telling the user about.
    """
    if profile.enabled and explicit_delay is not None:
        return (
            "note: --delay is ignored under humanization; use "
            "--humanize-post-delay LO,HI (or --no-humanize)."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    opts = resolve_options(args, load_config())
    try:
        profile = build_profile(opts)
    except ValueError as exc:
        print(f"Invalid humanization option — {exc}", file=sys.stderr)
        return EXIT_FATAL
    humanizer = Humanizer(profile)

    if not profile.enabled:
        print(
            "humanization is OFF for this run — faster, but easier for Instagram "
            "to fingerprint.",
            file=sys.stderr,
        )

    notice = delay_flag_notice(profile, args.delay)
    if notice:
        print(notice, file=sys.stderr)

    urls = _urls_from_file(args.file) if args.file else [args.url]
    if not urls:
        print("No Instagram URLs found.", file=sys.stderr)
        return EXIT_FATAL

    progress = Progress()

    # Authenticate once (may prompt / exit on failure).
    progress.start(f"logging in as @{opts['username']}" if opts["username"] else "logging in")
    client, account = get_client(
        session_file=opts["session_file"],
        browser=opts["browser"],
        username=opts["username"],
        password=opts["password"],
        progress=progress,
        humanizer=humanizer,
        device_profile=opts["device_profile"],
    )
    progress.ok("logged in" if opts["username"] else f"logged in as @{account}")

    # Remember working credentials + options for next time.
    if not args.no_save_config:
        progress.start("saving settings")
        save_config(config_updates(opts))
        progress.ok(f"→ {CONFIG_PATH}")

    if len(urls) > 1:
        if profile.enabled:
            print(
                f"Processing {len(urls)} URLs (humanized: {profile.request_delay}s/request, "
                f"{profile.post_delay}s between posts, "
                f"{profile.long_pause_prob:.0%} chance of a longer break)…"
            )
        else:
            print(
                f"Processing {len(urls)} URLs (not humanized: paced ~"
                f"{DELAY_RANGE[0]}–{DELAY_RANGE[1]}s/request, "
                f"{opts['delay']}s between posts)…"
            )

    skipped = 0
    for i, url in enumerate(urls):
        try:
            shortcode = parse_shortcode(url)
        except ValueError as exc:
            print(f"  ! Skipping: {exc}", file=sys.stderr)
            skipped += 1
            continue

        gate = resolve_gate(humanizer, progress)
        if gate.action == STOP:
            progress.done()
            print(
                f"  ⏹ Stopping: {gate.reason}. {len(urls) - i} URL(s) not fetched — "
                "re-run later, or pass --no-humanize.",
                file=sys.stderr,
            )
            return EXIT_PARTIAL
        humanizer.record("post")

        print(f"[{i + 1}/{len(urls)}] {shortcode}", flush=True)
        started = time.monotonic()
        try:
            # A rate-limit signal is something a human would just wait out.
            media, result = with_backoff(
                humanizer, progress,
                lambda: scrape(
                    client, shortcode, url, account,
                    sort=opts["comment_sort"], scan_limit=opts["comment_scan_limit"],
                    progress=progress, humanizer=humanizer,
                ),
            )
            out_dir = write_result(client, media, result, opts["output"], progress=progress)
            print(f"  ✓ {shortcode} -> {out_dir}  ({time.monotonic() - started:.0f}s)")
        except (igex.MediaNotFound, igex.ClientNotFoundError) as exc:
            progress.done()
            print(f"  ! Skipping {shortcode}: {exc}", file=sys.stderr)
            skipped += 1
        except (
            igex.LoginRequired,
            igex.ClientLoginRequired,
            igex.ChallengeRequired,
            igex.PleaseWaitFewMinutes,  # backoff exhausted
        ) as exc:
            # Genuinely fatal — the session is dead, or waiting didn't help.
            progress.done()
            print(f"  ✗ Fatal ({shortcode}): {exc}. Stopping.", file=sys.stderr)
            return EXIT_FATAL
        except Exception as exc:
            # Transient (network timeout, one bad post): skip and keep going.
            progress.done()
            print(f"  ! Skipping {shortcode}: {type(exc).__name__}: {exc}", file=sys.stderr)
            skipped += 1

        if i < len(urls) - 1:
            pace_between_posts(humanizer, opts["delay"])

    return EXIT_PARTIAL if skipped else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

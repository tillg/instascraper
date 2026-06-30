"""Command-line entrypoint: URL(s) -> <target-dir>/<shortcode>/ folders.

Options resolve in this order: explicit CLI flag > saved config (.env) >
environment variable > built-in default. After a successful login the resolved
options are saved back to ~/.config/insta_scraper/.env so they can be omitted
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

from insta_scraper.auth import get_client
from insta_scraper.config import CONFIG_PATH, ENV_KEYS, load_config, save_config
from insta_scraper.scraper import scrape
from insta_scraper.url import parse_shortcode
from insta_scraper.writer import write_result

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

  # Batch a file of URLs into data/, paced 8s between posts:
  instascrape --file SAMPLE_URLS.md --target-dir data --delay 8

  # Bootstrap auth from a logged-in browser instead of typing a password:
  instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/" --browser safari

  # Exact top-by-likes (scan every comment — slower, more requests):
  instascrape "<url>" --comment-scan-limit 0

output (one folder per post):
  <target-dir>/<shortcode>/
    post.md        caption + embedded media + top 10 comments + provenance header
    metadata.json  raw fields + provenance (machine-readable)
    <media files>  <shortcode>.mp4/.jpg; carousels get _1, _2, ... in order

auth & config:
  - First login needs --username/--password (or --browser). The session is then
    persisted to ~/.config/insta_scraper/session-<user>.json and reused, with a
    stable device id, so you are not re-logging-in (and tripping Instagram) each run.
  - 2FA / security-challenge codes (email or SMS) are prompted for when needed.
  - Options & credentials are saved to {CONFIG_PATH} (chmod 600) unless
    --no-save-config; precedence is: CLI flag > saved config > env var > default.

comment ranking:
  "likes" (default) = the 10 comments with the highest like_count among the first
  --comment-scan-limit scanned. This is a constructed ranking, NOT Instagram's
  in-app "top comments" order (which is not exposed). "instagram" = first
  returned (latest-first). The rule used is recorded in every post.md.

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
        "--delay", type=float, metavar="SECONDS",
        help="Pause between posts in batch mode, to stay gentle on Instagram. Default: 3",
    )
    p.add_argument(
        "--comment-sort", choices=["likes", "instagram"], metavar="MODE",
        help="How to pick the top 10 comments: 'likes' (default, by like_count) or "
        "'instagram' (first returned, latest-first)",
    )
    p.add_argument(
        "--comment-scan-limit", type=int, metavar="N",
        help="How many comments to scan before ranking by likes. Default: 200. "
        "0 = scan all (exact, but slower and more requests)",
    )
    p.add_argument(
        "--no-save-config", action="store_true",
        help="Do not write the resolved options/credentials to the config file",
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


def resolve_options(args, cfg, environ=None) -> dict:
    """Merge CLI args, saved config, and env into effective options."""
    environ = environ if environ is not None else os.environ
    return {
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
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    opts = resolve_options(args, load_config())

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
    )
    progress.ok("logged in" if opts["username"] else f"logged in as @{account}")

    # Remember working credentials + options for next time.
    if not args.no_save_config:
        progress.start("saving settings")
        save_config({ENV_KEYS[k]: opts[k] for k in ENV_KEYS if opts.get(k) is not None})
        progress.ok(f"→ {CONFIG_PATH}")

    if len(urls) > 1:
        print(f"Processing {len(urls)} URLs (paced ~1–3s/request, {opts['delay']}s between posts)…")

    skipped = 0
    for i, url in enumerate(urls):
        try:
            shortcode = parse_shortcode(url)
        except ValueError as exc:
            print(f"  ! Skipping: {exc}", file=sys.stderr)
            skipped += 1
            continue

        print(f"[{i + 1}/{len(urls)}] {shortcode}", flush=True)
        started = time.monotonic()
        try:
            media, result = scrape(
                client, shortcode, url, account,
                sort=opts["comment_sort"], scan_limit=opts["comment_scan_limit"],
                progress=progress,
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
            igex.PleaseWaitFewMinutes,
        ) as exc:
            # Genuinely fatal — the session is dead or we're rate-limited.
            progress.done()
            print(f"  ✗ Fatal ({shortcode}): {exc}. Stopping.", file=sys.stderr)
            return EXIT_FATAL
        except Exception as exc:
            # Transient (network timeout, one bad post): skip and keep going.
            progress.done()
            print(f"  ! Skipping {shortcode}: {type(exc).__name__}: {exc}", file=sys.stderr)
            skipped += 1

        if opts["delay"] and i < len(urls) - 1:
            time.sleep(opts["delay"])

    return EXIT_PARTIAL if skipped else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

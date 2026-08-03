<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo/instascraper-dark.svg">
    <img src="docs/logo/instascraper-light.svg" alt="instascraper" width="112" height="112">
  </picture>
</p>

# instascraper

Archive an Instagram **post** or **reel** from its URL into a self-contained
folder: all media (images + videos, including carousels), the caption, and the
top 10 comments, rendered as a readable `post.md` plus a `metadata.json`.

```
output/DXOCAyzEX8i/
├── post.md          # caption + embedded media + top 10 comments
├── DXOCAyzEX8i.mp4  # the reel video
├── DXOCAyzEX8i.jpg  # the video cover
└── metadata.json    # raw fields + provenance
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .          # installs deps + the `instascrape` command
```

This puts an `instascrape` executable in `.venv/bin/`, so after `activate` you
can just run `instascrape …`. On a pyenv-shimmed shell, a `python` function can
shadow the venv interpreter — using the `instascrape` command or
`./.venv/bin/python -m instascraper …` avoids that.

## Login & config

The tool authenticates as **you** and **persists the session**, so it logs in
only once. Later runs reuse the saved session (no password, works
non-interactively) and re-login only if the session dies — reusing the same
device identity each time so Instagram doesn't flag a "new device" on every run.

The first run takes your username + password; it then saves a durable session to
`~/.config/instascraper/session-<username>.json`, and (unless `--no-save-config`)
remembers your credentials and options in `~/.config/instascraper/.env`
(chmod 600) so you can omit them next time. If Instagram asks for a 2FA /
security-challenge code (email or SMS), you'll be prompted for it.

```bash
# First run — pass credentials once; they're saved for next time:
instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/" --username tillg --password 'xyz'

# Afterwards just pass URLs — username, password and options come from config:
instascrape "https://www.instagram.com/reel/DZ_KsKvKAW0/"
instascrape --file SAMPLE_URLS.md --target-dir data --delay 8
```

Option precedence: **CLI flag > saved config (`.env`) > environment variable >
built-in default**. Credentials never need to be re-typed once stored.

**Alternative: import a browser session** (one-shot bootstrap; less durable —
browser-imported sessions tend to get flagged by Instagram sooner):

```bash
instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/" --browser safari
# (also: chrome, brave, edge, firefox, chromium, opera, vivaldi)
```

## Usage

```bash
# Single URL
instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/"

# Batch every Instagram URL found in a file, into data/, paced:
instascrape --file SAMPLE_URLS.md --target-dir data --delay 8
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--username NAME` | stored config | Instagram account to log in as (saved) |
| `--password PW` | stored config | Password — only needed for the first login (saved) |
| `--target-dir DIR` | `output` | Base directory for the per-post folders (alias: `--output`) |
| `--session-file PATH` | `~/.config/instascraper/session-<user>.json` | Where the session is stored/reused |
| `--browser NAME` | off | Bootstrap login from a logged-in browser (safari, chrome, …) |
| `--delay SECONDS` | `3` | Pause between items in batch mode |
| `--comment-sort {likes,instagram}` | `likes` | Ranking rule for the top 10 (see below) |
| `--comment-scan-limit N` | `200` | Comments to scan before ranking; `0` = all (slow, rate-limit risk) |
| `--no-save-config` | off | Don't write credentials/options to the config file |

All saved options live in `~/.config/instascraper/.env`. `instascrape -h` shows
everything. Exit codes: `0` all good · `1` some items skipped · `2` fatal
(auth / rate limit — stopped early).

## Use as a library

`instascrape` is a thin CLI over a small, importable API — you can drive it from
your own Python instead of shelling out. Install it into your environment
(`pip install -e .`, or `pip install git+https://github.com/tillg/instascraper`)
and import from the `instascraper` package.

### Quick start: URL → folder

```python
from instascraper.auth import get_client
from instascraper.url import parse_shortcode
from instascraper.scraper import scrape
from instascraper.writer import write_result

# Logs in once and persists the session; later calls reuse it (no password).
# Omit username/password to use the saved session / IG_USERNAME / IG_PASSWORD.
client, account = get_client(username="me", password="pw")

url = "https://www.instagram.com/reel/DXOCAyzEX8i/"
media, result = scrape(client, parse_shortcode(url), url, account)
out_dir = write_result(client, media, result, output_base="output")
print("wrote", out_dir)
```

### Just the data (no files written)

`scrape()` returns a plain `ScrapeResult` dataclass — use it directly, e.g. to
push into your own DB or pipeline:

```python
media, result = scrape(client, parse_shortcode(url), url, account,
                       sort="likes", scan_limit=200)

print(result.owner, result.is_video, result.likes)
print(result.caption)
for c in result.comments:            # already ranked, top 10
    print(c.likes, c.username, c.text)

# Machine-readable dict (JSON-serializable), without downloading media:
from instascraper.writer import render_metadata
meta = render_metadata(result, media_files=[])
```

### Public API

| Import | Purpose |
|--------|---------|
| `auth.get_client(username=None, password=None, browser=None, session_file=None, progress=None) -> (Client, account)` | Log in / reuse a persisted session; returns an `instagrapi.Client` and the account name. |
| `url.parse_shortcode(url) -> str` | Extract the shortcode from a `/p/`, `/reel/` or `/tv/` URL. Raises `ValueError` if unrecognized. |
| `scraper.scrape(client, shortcode, source_url, account, sort="likes", scan_limit=200, progress=None) -> (media, ScrapeResult)` | Fetch metadata + ranked comments. No download. |
| `writer.write_result(client, media, result, output_base, progress=None) -> Path` | Download all media and write `post.md` + `metadata.json`. |
| `writer.render_markdown(result, media_files) / render_metadata(result, media_files)` | Pure renderers (no I/O / network). |
| `scraper.select_top_comments(comments, n=10, sort="likes")` | Pure comment-ranking helper. |
| `models.ScrapeResult`, `models.Comment`, `models.Provenance` | The data carriers. |

### Notes for integrators

- **Auth & sessions** — `get_client` persists the session to
  `~/.config/instascraper/session-<user>.json` (override with `session_file=`)
  and reuses it; pass `username`/`password` only when there's no valid session.
  First login may need a 2FA/challenge code (prompted on stdin) — supply
  credentials up front in headless setups, or pre-create the session once
  interactively.
- **Progress** — pass any object with `start(label)`, `ok(result)`, `tick()`,
  `stage(msg)`, `done()` as `progress=` to get callbacks; omit it for silence
  (the default `NullProgress`).
- **Errors** — `instagrapi` exceptions propagate (`MediaNotFound`,
  `LoginRequired`, `PleaseWaitFewMinutes`, …); `parse_shortcode` raises
  `ValueError`. Catch these to classify retry vs. skip vs. fatal.
- **Pacing** — the client uses `delay_range = [1, 3]`, so each request sleeps a
  random 1–3 s. Expect a single post to take tens of seconds; batch accordingly.

## About "top 10 comments"

Instagram's in-app "top comments" ranking is algorithmic and **not** exposed;
`get_comments()` returns latest-first. So "top" here is a *constructed*
measurement — by default, the 10 comments with the highest like count among the
first 200 scanned. Every `post.md` states the exact rule it used in its
provenance header, so the export is honest about what "top" means. Use
`--comment-sort instagram` for first-returned order instead.

## Notes & limitations

- **Personal use.** Automated collection is against Instagram's Terms of
  Service. This tool is for **personal archival** of content you can already see
  while logged in — it authenticates as you and does not bypass access controls,
  CAPTCHAs, or rate limits.
- **Personal data / EU-GDPR.** An export contains other people's usernames,
  comment text, and timestamps. Keeping a private archive is one thing;
  **republishing or sharing** it raises data-protection and copyright
  obligations and is out of scope for this tool.
- **Out of scope:** Stories/Highlights, whole-profile or hashtag crawls, comment
  replies/threads, any GUI.
- **Fallback:** if Instaloader's comment ordering or media coverage ever falls
  short, the scraper backend can be swapped to `instagrapi` without changing the
  CLI or output format (see `specs/changes/initial_scraper/architecture.md`).

## Development

```bash
pip install -r requirements.txt
python -m pytest          # network-free unit tests
```

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
instascrape --file SAMPLE_URLS.md --target-dir data
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

# Batch every Instagram URL found in a file, into data/:
instascrape --file SAMPLE_URLS.md --target-dir data
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--username NAME` | stored config | Instagram account to log in as (saved) |
| `--password PW` | stored config | Password — only needed for the first login (saved) |
| `--target-dir DIR` | stored config, else `output` | Base directory for the per-post folders (alias: `--output`) |
| `--session-file PATH` | `~/.config/instascraper/session-<user>.json` | Where the session is stored/reused |
| `--browser NAME` | off | Bootstrap login from a logged-in browser (safari, chrome, …) |
| `--device-profile {android,ios}` | `android` | Device family to emulate when creating a **new** session (see below) |
| `--comment-sort {likes,instagram}` | `likes` | Ranking rule for the top 10 (see below) |
| `--comment-scan-limit N` | `200` | Comments to scan before ranking; `0` = all (clamped under humanization) |
| `--delay SECONDS` | `3` | Pause between items in batch mode — **only with `--no-humanize`** |
| `--no-humanize` | off | Turn humanization off (see below) |
| `--no-save-config` | off | Don't write credentials/options to the config file |

All saved options live in `~/.config/instascraper/.env` — `--target-dir`
included, so once you pass it that directory stays your default for later runs
(it's `INSTASCRAPE_OUTPUT` in the `.env`; edit or delete the line to go back to
`output`). The path is resolved **relative to the current working directory**, so
the same setting writes into a different folder when you run from elsewhere.
`instascrape -h` shows everything. Exit codes: `0` all good · `1` some items skipped · `2` fatal
(auth / rate limit — stopped early).

## Humanization

Instagram's automated defenses don't just look at *what* you fetch, they look at
*how*. A metronomic 1–3 s drip with no idle, thousands of requests to page every
comment on a post, and a 200-URL batch at 04:00 are all cheap to spot. A live
capture of the real web client shows the opposite shape: a short burst of
requests when you open a post, then **4–57 s of nothing** until the next action.

So by default `instascrape` paces itself like a person using the app:

- **Sampled think-time** instead of fixed sleeps — `1–4 s` between API calls,
  `2–8 s` between comment pages, `20–90 s` between posts, plus a 20% chance of a
  longer 30–120 s "got distracted" break.
- **Human-scale comment depth** — each page has a 30% chance of being the last,
  and `--comment-scan-limit 0` becomes ~200 rather than "page every comment".
- **Rate ceilings** — 300 requests / 60 posts per session, 200 requests per
  rolling hour.
- **Active hours** — 08:00–23:00 local, with jittered edges. Outside them the
  run ends gracefully (exit `1`) instead of blocking for hours.
- **Politeness backoff** — a `PleaseWaitFewMinutes` is waited out (60 s, doubling,
  capped at 15 min, 3 attempts) instead of being immediately fatal.

Every one of these is a `--humanize-*` flag (or `INSTASCRAPE_HUMANIZE_*` in
`.env`); `instascrape -h` lists them all, and the defaults live in one place,
`instascraper.behavior.BehaviorProfile`. Each `post.md` records the pacing it
was fetched with, and how many comments were *actually* scanned.

`--no-humanize` applies **to that run only** and is deliberately *not* saved to
your config, unlike every other option — humanization staying the default is the
whole point, and a one-off opt-out silently leaking into later runs would defeat
it. (A permanent opt-out is possible, but it takes a hand-written
`INSTASCRAPE_HUMANIZE=false` in the `.env`; `--humanize` overrides that.)

### Prefer one batch over many runs

Rate ceilings, the rolling hourly window, and the between-post idle all live in a
**single process**. Ten separate `instascrape` invocations back-to-back therefore
get *no* inter-post pacing at all (there is no "next post" in a one-URL run), ten
fresh app-open warm-ups, and ten reset counters — a worse signal than not pacing
at all. Put the URLs in one file and make one run of it:

```bash
instascrape --file urls.md          # ✅ paced, gated, one warm-up
for u in $(cat urls.txt); do instascrape "$u"; done   # ❌ don't
```

```bash
# Idle 30–120s between posts instead of the default 20–90s:
instascrape --file urls.md --humanize-post-delay 30,120

# Archive round the clock, no active-hours window:
instascrape --file urls.md --humanize-active-hours off

# Old fast behavior — fixed --delay, and 0 really means every comment:
instascrape "<url>" --no-humanize --delay 3 --comment-scan-limit 0
```

**This lowers, but cannot eliminate, the chance of being flagged.** Account
history, IP reputation, and device identity matter too, and are only partly in
this tool's control. Humanized runs are also deliberately *slower*.

### Device identity

Instagram treats a new device as its own signal, independent of pacing — a fresh
login can trip a new-device alert before any scraping happens. So the emulated
device is **stable**, not randomized: it's seeded once when a session is created
and persisted, and a reused session is never re-fingerprinted (changing a live
session's device is itself a new-device event). If `--device-profile` disagrees
with the session on disk, the tool says so and keeps the session; switching is a
deliberate act — delete `session-<user>.json` and log in again, expecting one
new-device prompt.

`android` is the default because it is the only *coherent* choice: instagrapi
speaks Instagram's Android private API and sends Android headers
(`X-IG-Android-ID`, `X-IG-Capabilities`) on every request. `--device-profile ios`
changes the user-agent only, leaving it contradicting the rest of the envelope —
it's available, but it warns, and it isn't recommended.

### Request identity

Pacing is only half the picture: *what* a request looks like matters as much as
*when* it is sent, and no amount of waiting fixes a request that identifies the
library sending it. So the client is a thin subclass of instagrapi's that cleans
up its envelope. There is nothing to configure — it is always on:

- **Forged per-user tokens are not sent.** instagrapi fills `IG-U-SHBID`,
  `IG-U-SHBTS`, `IG-U-RUR` and `IG-U-IG-DIRECT-REGION-HINT` with HMAC blobs
  hardcoded in the library, captured from someone else's session — sent under
  *your* account id. Instagram mints these per account, so they are a mismatch on
  a value that can't be forged. They're dropped; `IG-U-RUR` comes back as soon as
  Instagram issues a real one.
- **One telemetry session per run.** Upstream regenerates
  `X-Pigeon-Session-Id` on *every* request; a real app holds one for as long as
  it's in the foreground.
- **The WWW-claim gets echoed.** Instagram answers with `x-ig-set-www-claim` and
  expects it back; upstream never reads it outside its bloks flow, so every
  request forever says `X-IG-WWW-Claim: 0`.
- **A navigation chain that matches the request.** Upstream sends a constant
  claiming you navigated your own profile and following list, on every request
  including cold post fetches.
- **Media downloads look like Instagram.** Upstream fetches media bytes with
  bare `requests`, announcing `python-requests/x.y` to the CDN seconds after an
  "Instagram Android" API call from the same IP — the two correlate perfectly.
  Downloads now carry the app's own user-agent.

What this does *not* fix: instagrapi's device model, app version and bloks hash
are the same for everyone using the library. Rotating them would be a new-device
event, which is the thing "Device identity" above exists to avoid — so they stay
put. If you need to be unrecognisable rather than merely less obvious, a real
browser session is the stronger tool.

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
| `auth.get_client(username=None, password=None, browser=None, session_file=None, progress=None, humanizer=None, device_profile="android") -> (Client, account)` | Log in / reuse a persisted session; returns an `instagrapi.Client` and the account name. |
| `url.parse_shortcode(url) -> str` | Extract the shortcode from a `/p/`, `/reel/` or `/tv/` URL. Raises `ValueError` if unrecognized. |
| `scraper.scrape(client, shortcode, source_url, account, sort="likes", scan_limit=200, progress=None, humanizer=None) -> (media, ScrapeResult)` | Fetch metadata + ranked comments. No download. |
| `writer.write_result(client, media, result, output_base, progress=None) -> Path` | Download all media and write `post.md` + `metadata.json`. |
| `writer.render_markdown(result, media_files) / render_metadata(result, media_files)` | Pure renderers (no I/O / network). |
| `scraper.select_top_comments(comments, n=10, sort="likes")` | Pure comment-ranking helper. |
| `behavior.BehaviorProfile`, `behavior.Humanizer`, `behavior.build_profile(opts)` | The pacing policy and the object that applies it. |
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
  `ValueError`. Catch these to classify retry vs. skip vs. fatal. `scrape` fetches
  metadata with `media_info_v1` (private API) rather than `media_info`, so you get
  the real error instead of the `ClientJSONDecodeError` that instagrapi's dead
  web-GraphQL fallback produces.
- **Pacing** — the library path is **unhumanized by default**: pass no
  `humanizer` and you get today's behavior (`delay_range = [1, 3]`, exhaustive
  comment paging, no rate ceilings). To pace like the CLI does, build one and
  hand it to both `get_client` and `scrape`:

  ```python
  from instascraper.behavior import BehaviorProfile, Humanizer

  humanizer = Humanizer(BehaviorProfile())          # or build_profile(opts)
  client, account = get_client(username="me", humanizer=humanizer)
  media, result = scrape(client, shortcode, url, account, humanizer=humanizer)
  ```

  One `Humanizer` per run — it carries the session counters and the rolling rate
  window. `Humanizer(profile, rng=…, sleep=…, now=…, wall=…)` takes an injected
  RNG and clock, so tests stay deterministic and never actually sleep. Either
  way, expect a single post to take tens of seconds; batch accordingly.

## About "top 10 comments"

Instagram's in-app "top comments" ranking is algorithmic and **not** exposed;
`get_comments()` returns latest-first. So "top" here is a *constructed*
measurement — the 10 comments with the highest like count among the ones
actually scanned (up to 200 by default, and often fewer, since humanization
stops paging early the way a reader would). Every `post.md` states the rule it
used **and how many comments were really paged**, so the export never overstates
its depth. Use `--comment-sort instagram` for first-returned order instead.

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

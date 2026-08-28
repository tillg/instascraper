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

Requires **Python ≥ 3.10**. Not on PyPI — deliberately, since automated
collection is against Instagram's Terms and this is a personal-archive tool; the
install is from git, so the audience stays deliberate.

**Use the CLI** (isolated, on your `PATH`):

```bash
pipx install git+https://github.com/tillg/instascraper@v1.0.0
instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/"
```

**Use it from another project** — add it as a dependency and pin the tag:

```toml
# pyproject.toml
dependencies = ["instascraper @ git+https://github.com/tillg/instascraper@v1.0.0"]
```

```bash
pip install "instascraper @ git+https://github.com/tillg/instascraper@v1.0.0"
```

Either brings `instagrapi` and `browser_cookie3` with it, and installs both the
`instascrape` command and the importable `instascraper` package (see
[Use as a library](#use-as-a-library)). Pin a tag rather than `@main`: pacing
defaults and the ledger schema can change between releases.

**Hack on it:**

```bash
git clone https://github.com/tillg/instascraper && cd instascraper
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'   # deps + the `instascrape` command + pytest
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
`~/.config/instascraper/session-<username>.json`, keeps its pacing state in
`~/.config/instascraper/activity-<username>.json`, and (unless
`--no-save-config`) remembers your credentials and options in
`~/.config/instascraper/.env`
(chmod 600) so you can omit them next time. If Instagram asks for a 2FA /
security-challenge code (email or SMS), you'll be prompted for it.

Sessions do eventually die, and the next run then needs a password. In a cron job
or any other run without a terminal there is nothing to prompt on, so the tool
says so and stops (exit `1`) instead of failing obscurely. For unattended runs,
set `IG_PASSWORD` in the environment or the config `.env`; otherwise run
`instascrape` interactively once to mint a fresh session.

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
| `--no-activity-ledger` | off | Skip the cross-session pacing state for this run (see below) |
| `--activity-file PATH` | `~/.config/instascraper/activity-<account>.json` | Where the activity ledger lives |
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
  rolling hour, and 1000 requests / 150 posts per day. A "session" here is a
  *sitting*, not a process: activity with no gap longer than 30 minutes.
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

### Pacing is continuous across runs

Ten separate `instascrape` invocations pace like one ten-URL batch, because the
pacing state outlives the process. A small **activity ledger** per account —
`~/.config/instascraper/activity-<account>.json`, chmod 600, next to the session
file — carries the last action, the counters, and the rolling window from one run
to the next. So a loop over URLs is fine:

```bash
instascrape --file urls.md                              # ✅ paced, gated
for u in $(cat urls.txt); do instascrape "$u"; done     # ✅ also paced now
```

What that buys, concretely:

- **Owed idle.** A run started seconds after the last one waits out the rest of a
  post-scale pause *before its first request* — including the request that only
  validates the saved session, since that one is already real traffic. It says so
  (`continuing a recent session — idling 47s first`), so it never looks like a hang.
- **Ceilings that actually bind**, per sitting, per hour, and **per day**. The two
  day ceilings (1000 requests, 150 posts) only mean something with a persisted
  ledger — which is why they exist now and did not before.
- **One app-open, not ten.** Warm-up fires only on a *cold open*: a gap longer
  than 5 minutes, or a fresh login. Ten runs in three minutes warm up once.
- **A coherent daily rhythm.** The jitter on the active-hours edges is derived
  from the ledger's salt and today's date, so the boundary sits in one place all
  day (a person whose bedtime is 23:14 today) instead of moving every run.

**One run at a time.** A run holds an advisory lock on its ledger, so a second
`instascrape` for the same account exits `2` with a clear message rather than
interleaving — two simultaneous clients for one account is itself a signal, and a
person has one phone. This applies to `--no-humanize` runs too.

The ledger holds **only timestamps, counters, and a random salt** — no URLs, no
shortcodes, no captions, no comments. Deleting the file is a full reset (the day's
budget included); `--activity-file PATH` moves it, `--no-activity-ledger` skips it
for one run.

`--no-humanize` stops the *waiting and the gating*, but still **records** activity,
so a later humanized run isn't lied to about the day's budget: without that, a
60-post unpaced burst would be invisible and the next run would grant itself a
cold open and a fresh budget it hadn't earned. Accounting is not pacing, so the
two have separate switches — `--no-humanize --no-activity-ledger` together give
the pre-humanization tool with no file at all.

The day ceilings are **honest guesses, not measurements**: the live capture the
other defaults are calibrated against is a single session and says nothing about
daily volume. They are the first numbers to raise or lower for your own use
(`--humanize-max-posts-per-day`, `--humanize-max-requests-per-day`).

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

Everything below is importable and covered by tests. Signatures are exact; there
is no wildcard re-export, so import from the module (`from instascraper.scraper
import scrape`).

**`instascraper.url` — URL parsing (pure)**

| Symbol | Purpose |
|--------|---------|
| `parse_shortcode(url: str) -> str` | Shortcode from a `/p/`, `/reel/` or `/tv/` URL. Raises `ValueError` if unrecognized. |

**`instascraper.auth` — login & session**

| Symbol | Purpose |
|--------|---------|
| `get_client(session_file=None, browser=None, username=None, password=None, progress=None, humanizer=None, device_profile="android") -> (Client, account)` | Reuse a persisted session → else a browser-cookie bootstrap → else password login. Returns a `fingerprint.Client` and the account name. Records the session-validation request and warms up on a cold open when given a `humanizer`. |
| `device_family(settings: dict) -> str` | `"android"` / `"ios"` for a persisted session's settings. |
| `make_links_clickable(message: str) -> str` | Absolutize the relative challenge URLs Instagram puts in error strings. |
| `DEFAULT_SESSION_DIR`, `DELAY_RANGE`, `DEVICE_PROFILES`, `SUPPORTED_BROWSERS`, `REQUEST_TIMEOUT`, `IOS_DEVICE`, `IOS_USER_AGENT` | Defaults and the supported enumerations. |

**`instascraper.scraper` — fetch (network) + ranking (pure)**

| Symbol | Purpose |
|--------|---------|
| `scrape(client, shortcode, source_url, account, sort="likes", scan_limit=200, progress=None, humanizer=None) -> (media, ScrapeResult)` | Metadata via `media_info_v1` + paged comments, ranked. Downloads nothing. The `media` object it returns carries the URLs `write_result` needs. |
| `select_top_comments(comments, n=10, sort="likes") -> list[Comment]` | The ranking rule itself. Pure. |
| `NullProgress` | The no-op progress sink used when `progress=None`. |

**`instascraper.writer` — download + render**

| Symbol | Purpose |
|--------|---------|
| `write_result(client, media, result, output_base, progress=None) -> Path` | Downloads every media item by URL and writes `post.md` + `metadata.json`. Returns the output folder. |
| `render_markdown(result, media_files: list[str]) -> str` | `post.md` as a string. Pure. |
| `render_metadata(result, media_files: list[str]) -> dict` | JSON-serializable dict. Pure. |
| `MEDIA_EXTS`, `IMAGE_EXTS`, `VIDEO_EXTS` | Extension sets used when globbing the output folder. |

**`instascraper.models` — the data contract**

| Symbol | Fields |
|--------|--------|
| `ScrapeResult` | `shortcode`, `source_url`, `owner`, `typename`, `taken_at`, `likes`, `is_video`, `caption`, `comments: list[Comment]`, `provenance: Provenance \| None` |
| `Comment` | `username`, `likes`, `text`, `created_at` |
| `Provenance` | `fetched_at`, `backend`, `account`, `comment_sort`, `comment_scan_limit`, `comments_scanned`, `humanization`, `tool` |

**`instascraper.behavior` — pacing policy and the object that applies it**

| Symbol | Purpose |
|--------|---------|
| `BehaviorProfile(...)` | Frozen dataclass holding *every* pacing parameter — the single source of default truth. `.summary()` renders the provenance line. |
| `Humanizer(profile=None, rng=None, sleep=time.sleep, now=time.time, wall=datetime.now, ledger=None)` | Applies a profile. RNG and both clocks are injected, which is why the test suite is deterministic and never sleeps. |
| `Humanizer.delay(kind) -> float` / `.sample_delay(kind) -> float` | Sleep a sampled think-time / sample it without sleeping. `kind ∈ {request, page, post, read_pause, warmup}`. |
| `Humanizer.gate(kind) -> GateResult` | Verdict before an action: active hours → day → session → rolling window. |
| `Humanizer.record(kind)` | Count an action. Unconditional — accounting is not pacing — and flushes the ledger after a post. |
| `Humanizer.owed_idle() -> float` | Seconds still owed before this run's first request (see [cross-session pacing](#pacing-is-continuous-across-runs)). |
| `Humanizer.is_new_session()` / `.is_cold_open()` | Same sitting? / was the app still open? Two thresholds, one measured gap. |
| `Humanizer.should_stop_early()`, `.clamp_scan_limit(n)` | Human-scale comment depth. |
| `Humanizer.can_backoff(attempt)`, `.backoff(attempt)`, `.wait(seconds)` | Politeness backoff and externally-decided waits. |
| `Humanizer.warmup(client) -> int` | The app-open calls; never fails a run. |
| `Humanizer.pacing_summary() -> str` | What goes into `Provenance.humanization`, profile + ledger state. |
| `Range(lo, hi)`, `.sample(rng)`, `.sample_int(rng)` | The sampled band used for every delay. |
| `GateResult(action, seconds, reason)` and `PROCEED` / `WAIT` / `STOP` | The gate verdict. `WAIT` is only ever the rolling window, so it is bounded. |
| `build_profile(opts: dict) -> BehaviorProfile` | Build a profile from resolved CLI/`.env`/env options; raises `ValueError` on a malformed value. |

**`instascraper.activity` — cross-session pacing state**

| Symbol | Purpose |
|--------|---------|
| `ActivityLedger(path, *, window_seconds, lock_timeout=None, now=time.time, sleep=time.sleep, enabled=True)` | The per-account ledger. Use it as a context manager: `__enter__` locks, loads and prunes; `__exit__` flushes and unlocks. `lock_timeout=None` means `DEFAULT_LOCK_TIMEOUT`. |
| `.load() -> Activity`, `.flush()`, `.close()`, `.activity`, `.path`, `.lock_path` | Explicit control if you are not using `with`. `flush()` is atomic (temp + `os.replace`, chmod 600). |
| `Activity` | The persisted document: `version`, `salt`, `last_action`, `session_requests/posts`, `day`, `day_requests/posts`, `window`. `.to_dict()` / `.from_dict(raw)` — the latter never raises. |
| `activity_path(username, override=None) -> Path` | `activity-<username>.json` under `~/.config/instascraper`, or the override. |
| `LedgerBusy` | Raised when another run holds the lock. |
| `LEDGER_VERSION`, `DEFAULT_LOCK_TIMEOUT` | Schema version; the 5 s lock wait. |

**`instascraper.config` — the saved `.env`**

| Symbol | Purpose |
|--------|---------|
| `load_config(path=CONFIG_PATH) -> dict[str, str]` / `save_config(updates, path=CONFIG_PATH)` | Read/merge-write the config file (chmod 600). |
| `ENV_KEYS`, `CONFIG_DIR`, `CONFIG_PATH` | Option-key → env-var mapping, and where things live. |

**`instascraper.fingerprint` — what a request looks like**

| Symbol | Purpose |
|--------|---------|
| `Client(...)` | The `instagrapi.Client` subclass `get_client` returns: drops upstream's forged per-user tokens, one Pigeon session id per run, echoes the real `WWW-Claim`, and fetches media as the app. |
| `photo_download_by_url` / `video_download_by_url` (+ `_origin` variants) | Media fetches over the app-shaped CDN transport. |
| `FORGED_HEADERS`, `NAV_CHAIN`, `CDN_HEADERS` | The header policy, if you need to inspect or extend it. |

**`instascraper.cli` — the CLI, reusable in pieces**

| Symbol | Purpose |
|--------|---------|
| `main(argv=None) -> int` | The whole CLI. Exit codes `EXIT_OK` / `EXIT_PARTIAL` / `EXIT_FATAL` (`0` / `1` / `2`). |
| `build_parser()`, `resolve_options(args, cfg, environ=None)`, `config_updates(opts)` | Argument parsing and the **CLI > `.env` > env > default** resolution, if you want the same precedence in your own entry point. |
| `Progress` | The terminal progress sink (`start`/`ok`/`tick`/`stage`/`done`). |
| `resolve_gate`, `gate_before_login`, `pay_owed_idle`, `pace_between_posts`, `with_backoff` | The pacing choreography around a scrape, should you build your own loop. |

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
- **Cross-session pacing is opt-in for library callers.** Importing the package
  never touches `~/.config`: with no `ledger=`, a `Humanizer` behaves exactly as
  it did before the activity ledger existed (owed idle `0`, RNG-drawn
  active-hours edges, no file, no lock). Only the CLI enables it by default. If
  your own process runs repeatedly and you want the CLI's continuity — one
  timeline, shared ceilings, one warm-up — open a ledger around the work:

  ```python
  from instascraper.activity import ActivityLedger, LedgerBusy, activity_path
  from instascraper.behavior import BehaviorProfile, Humanizer

  profile = BehaviorProfile()
  try:
      with ActivityLedger(activity_path("me"),
                          window_seconds=profile.window_seconds) as ledger:
          humanizer = Humanizer(profile, ledger=ledger)
          humanizer.wait(humanizer.owed_idle())   # pay before the first request
          client, account = get_client(username="me", humanizer=humanizer)
          ...
  except LedgerBusy:
      ...   # another run holds this account's ledger — a person has one phone
  ```

  Order matters: `owed_idle()` is paid *before* `get_client`, because the
  session-validation request inside it is already real traffic. Check
  `humanizer.gate("request")` first if you want a day ceiling to stop you before
  spending that request.

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
- **A local record of your own activity.** The activity ledger is new data at
  rest: timestamps and counters for your own runs (never URLs or content), chmod
  600 under `~/.config/instascraper/`. Delete `activity-<account>.json` to wipe
  it, or run with `--no-activity-ledger` to never write it.
- **Personal data / EU-GDPR.** An export contains other people's usernames,
  comment text, and timestamps. Keeping a private archive is one thing;
  **republishing or sharing** it raises data-protection and copyright
  obligations and is out of scope for this tool.
- **Out of scope:** Stories/Highlights, whole-profile or hashtag crawls, comment
  replies/threads, any GUI.
- **Fallback:** if Instaloader's comment ordering or media coverage ever falls
  short, the scraper backend can be swapped to `instagrapi` without changing the
  CLI or output format (see `specs/changes/initial_scraper/architecture.md`).

## License

[MIT](LICENSE) — © 2026 Till Gartner. Do what you like with the code; keep the
notice, and note the "AS IS, WITHOUT WARRANTY OF ANY KIND" clause: this tool
talks to Instagram's private API and can get an account rate-limited or flagged.

The licence covers *this code*, not what you do with it. Instagram's Terms still
govern the use — see [Notes & limitations](#notes--limitations).

## Development

```bash
pip install -e '.[dev]'                  # deps + pytest
.venv/bin/python -m pytest -q            # 277 network-free, sleep-free tests
```

Use the venv interpreter, not a bare `python`: a pyenv shim lacks the deps and
fails at import. `tests/conftest.py` enforces the suite's invariants — no real
`~/.config`, no real `time.sleep`, no sockets — so a test that reaches for any of
them fails loudly rather than quietly touching your account state.

### Releasing

Version lives in two places that a test keeps in sync (`pyproject.toml` and
`instascraper/__version__`). To cut a release: bump both, run the suite, commit,
then tag — consumers pin the tag:

```bash
git tag -a v1.0.0 -m "…" && git push origin main --tags
```

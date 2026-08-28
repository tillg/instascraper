# System Functional Description: instascrape

What the tool does, from a user's point of view.

## Capabilities

- **Archive one URL**: `instascrape "<post/reel/tv URL>"` → a folder with media,
  caption, and top-10 comments.
- **Batch a file of URLs**: `--file <path>` scrapes every Instagram URL found in
  a text/markdown file, paced between posts.
- **All media**: single image, reel video (+ cover), or every item of a carousel.
- **Comment ranking**: top 10 by like count (`--comment-sort likes`, default) or
  first-returned (`--comment-sort instagram`); depth via `--comment-scan-limit`.
- **Durable login**: log in once (password, with 2FA/challenge); reused after,
  with a stable emulated device that is never silently re-fingerprinted.
- **Humanized pacing (on by default)**: sampled think-time between requests,
  pages, and posts; human-scale comment depth; per-sitting, per-hour and per-day
  rate ceilings; an active-hours window; and a polite wait-and-retry after a
  rate-limit signal. Every parameter is a `--humanize-*` flag; `--no-humanize`
  stops the waiting and the gating for that run.
- **Pacing that continues across runs (on by default)**: a per-account activity
  ledger (`activity-<account>.json`) carries the last action, the counters, and
  the rolling window between invocations, so a loop over URLs paces like one
  batch — an owed idle before the first request, shared ceilings, one app-open
  warm-up instead of one per run, and a stable active-hours edge for the day.
  One run at a time per account. `--no-activity-ledger` opts out for a run.
- **Remembered settings**: credentials + options saved to `.env` and reused.
- **Live progress**: each step announces itself and completes on the same line;
  comment scanning shows one dot per page fetched.

## Primary user journey

```mermaid
flowchart LR
    A[run instascrape URL] --> B{session valid?}
    B -- yes --> D[fetch metadata]
    B -- no --> C[login: password +/− 2FA] --> D
    D --> E[scan + rank comments]
    E --> F[download media]
    F --> G[write post.md + metadata.json]
    G --> H["✓ output/<shortcode>/"]
```

## Inputs

| Input | Where | Notes |
|-------|-------|-------|
| Post/reel URL or `--file` | CLI (one required) | the work to do |
| `--username` / `--password` | CLI / `.env` / env | first login only; then saved & reused |
| `--target-dir` (`--output`) | CLI / `.env` | output base; default `output` |
| `--browser` | CLI | bootstrap login from a logged-in browser |
| `--session-file` | CLI / `.env` | override session location |
| `--device-profile` | CLI / `.env` | device family for a **new** session (`android` default) |
| `--delay` | CLI / `.env` | seconds between posts — **only with `--no-humanize`** |
| `--comment-sort`, `--comment-scan-limit` | CLI / `.env` | ranking rule + depth |
| `--no-humanize`, `--humanize-*` | CLI / `.env` | pacing policy; defaults in `behavior.BehaviorProfile` |
| `--no-activity-ledger` | CLI | skip cross-session pacing state for this run |
| `--activity-file`, `--activity-lock-timeout` | CLI / `.env` | ledger location; how long to wait for another run's lock |
| `--no-save-config` | CLI | don't persist options/credentials |

Resolution precedence: **CLI flag > saved `.env` > environment variable >
built-in default**. `instascrape -h` documents everything. Humanization options
are left unset unless chosen, so `BehaviorProfile` stays the single source of
default truth and unused keys stay out of the saved `.env`.

Two deliberate exceptions to "options are remembered":

- **`--no-humanize` is never persisted** (`cli._NEVER_SAVED`). Humanization being
  the default is the point of the feature; a one-off opt-out silently leaking
  into every later run would defeat it. It applies per run, and is announced on
  stderr each time. A permanent opt-out requires a hand-written
  `INSTASCRAPE_HUMANIZE=false`, which `--humanize` overrides.
- **`--no-activity-ledger` is never persisted either**, for the same reason:
  cross-session pacing being the default is the point of it. Note what it does
  *not* cover — `--no-humanize` stops the waiting and the gating but still
  **records** activity, so a later humanized run is not lied to about the day's
  budget. Accounting is not pacing, so they are two switches; both together
  reproduce the pre-humanization tool. Every run, unhumanized ones included,
  takes the ledger's run lock, so a second concurrent run for one account exits
  `2` — which is a deliberate narrowing of "the old fast behavior".

## Outputs

- `<target-dir>/<shortcode>/post.md` — provenance header (including how many
  comments were actually scanned and the pacing used), caption, embedded
  `## Media`, and `## Top N comments`.
- `<target-dir>/<shortcode>/metadata.json` — raw fields + `provenance` block.
- The media files (`<shortcode>[_n].<ext>`, plus a `.jpg` cover for videos).

Exit codes: `0` all good · `1` some items skipped (not found / private /
transient), **or the run ended gracefully** at a rate ceiling (per sitting, per
hour, or per day — possibly *before login*, since the pre-login gate never
sleeps out a full window) or outside the active-hours window · `2` fatal (auth
failed / rate-limited past the backoff attempts, or another run for the same
account holds the ledger lock).

## Out of scope

Stories/Highlights, whole-profile or hashtag crawls, nested comment replies, any
GUI. Sharing/republishing exports raises data-protection obligations and is the
user's responsibility (personal-archive tool).

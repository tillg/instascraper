# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # core deps + pytest
```

- **Run tests with the venv interpreter, not a bare `python`.** A pyenv-shimmed
  `python` lacks the deps and fails at import with
  `ModuleNotFoundError: No module named 'instagrapi'`. The suite is network-free
  and fast:
  ```bash
  .venv/bin/python -m pytest -q                          # whole suite
  .venv/bin/python -m pytest tests/test_scrape.py -q     # one file
  .venv/bin/python -m pytest -k comments -q              # by keyword
  .venv/bin/python -m pytest tests/test_cli.py::test_name -q   # one test
  ```
- Run the tool: after `activate`, `instascrape "<url>"`; or without activating,
  `./.venv/bin/python -m instascraper "<url>"`.
- No linter or formatter is configured.

**Naming:** the package is `instascraper`, the console script is `instascrape`
(no `r`). Imports are `instascraper.*`; the entry point is `instascraper.cli:main`.

## Architecture

Turns an Instagram post/reel URL into a self-contained folder (media + caption +
top-10 comments + provenance). One module per stage; `cli.py` orchestrates a
per-URL pipeline:

```
cli.main → behavior.build_profile → auth.get_client
        → (per URL) url.parse_shortcode → scraper.scrape → writer.write_result
```

Cross-cutting decisions, each of which spans several files:

- **Backend is instagrapi (private mobile API), not instaloader.** Instaloader's
  web-GraphQL post fetch returns empty against current Instagram, so all
  fetching and media download go through an `instagrapi.Client`.
  **Corollary — call `media_info_v1`, never `media_info`.** instagrapi's
  `media_info` falls back to *web* GraphQL, the very path that doesn't work; it
  answers `200` with a ~600KB HTML login wall, so a plain `MediaNotFound` — or a
  dead session — comes back as an opaque `ClientJSONDecodeError` and gets
  misclassified as a transient skip. So: `scraper.scrape` fetches metadata with
  `media_info_v1`, and `writer._download_media` downloads via
  `photo_download_by_url` / `video_download_by_url` using the URLs already on the
  `media` object — **never** `album_download` / `photo_download` /
  `video_download`, which each re-fetch metadata through `media_info`
  (`photo_download` tries web GraphQL *first*). Both test suites booby-trap the
  banned helpers so this can't silently regress.
- **`models.py` is the contract.** `ScrapeResult` / `Comment` / `Provenance`
  decouple `scraper.py` (fetch only — produces them) from `writer.py` (downloads
  media + renders `post.md`/`metadata.json`). `scraper` never downloads media;
  `writer` globs the output dir. The renderers and `select_top_comments` are
  **pure**, which is what keeps the whole test suite network-free.
- **Option precedence: CLI flag > saved `.env` > env var > built-in default**
  (`resolve_options`/`_pick` in `cli.py`; keys in `config.ENV_KEYS`). CLI args
  default to `None` so "user set it" is distinguishable from "default". After a
  successful login the resolved options are written back to
  `~/.config/instascraper/.env` (chmod 600) unless `--no-save-config`.
- **Durable auth with a stable device.** `auth.get_client` tries, in order:
  (1) reuse a persisted session — `get_timeline_feed()` validates it, no password,
  works headless; (2) `--browser` cookie bootstrap; (3) password login (2FA /
  challenge prompted interactively). On re-login it **keeps the same device
  UUIDs** so Instagram doesn't see a "new device" each run. Session lives at
  `~/.config/instascraper/session-<user>.json`.
- **`behavior.py` owns all pacing; no call site holds a timing constant.**
  `BehaviorProfile` is a frozen dataclass of sampled `Range`s and ceilings — the
  single source of default truth. `Humanizer` applies it (think-time, early
  comment give-up, rate gating, backoff, warm-up) with an **injected RNG, sleep,
  and clock**, which is what keeps the suite deterministic *and sleep-free*.
  Humanization is **on by default**; `humanizer=None` is the library path and
  reproduces the pre-humanization behavior exactly. Defaults are calibrated
  against `specs/system/observations-web-cadence.md`.
- **Identity is stable, behavior is varied — they pull opposite ways on purpose.**
  Everything in `BehaviorProfile` is *sampled*; `device_profile` and the session
  UUIDs are *fixed*. `_apply_device` runs **only when minting a new session**: a
  reused session is authoritative, because re-fingerprinting a live session is
  itself the new-device event we're avoiding. `android` is the default —
  instagrapi sends Android headers regardless of the UA, so `ios` is UA-only and
  warns.
- **"Top 10 comments" is a constructed ranking, not Instagram's.**
  `select_top_comments(sort="likes")` = the 10 highest `like_count` among the
  comments **actually scanned**. `sort="instagram"` = first-returned order. The
  rule, the configured limit, and `comments_scanned` (which differs, since
  early-stop and the `scan_depth_clamp` on `0` cut paging short) all go into
  `Provenance` — provenance must never overstate depth.
- **Error handling drives exit codes** (the `cli.main` loop): not-found / private
  → skip that URL; `PleaseWaitFewMinutes` → jittered backoff and retry, fatal only
  once attempts are spent; `LoginRequired` / `ChallengeRequired` → fatal, stop the
  batch; any other exception → skip & continue. A rate ceiling or the active-hours
  window → *graceful* stop. Exit `0` = all good, `1` = some skipped or a graceful
  stop, `2` = fatal.
- **Two options are deliberately never persisted** (`cli._NEVER_SAVED`), unlike
  every other: `--no-humanize` applies per run only. Humanization being the
  default is the point of the feature, so a one-off opt-out must not leak into
  later runs. `--humanize` overrides a hand-written `INSTASCRAPE_HUMANIZE=false`.
- **Pacing state is per process.** Ceilings, the rolling window, and inter-post
  idle live in the one `Humanizer` a run builds, so *N* invocations don't share a
  budget and a one-URL run gets no idle at all. Batch with `--file`; see
  `specs/changes/cross-session-humanization/` for the proposed fix.
- **Progress UI:** `cli.Progress` prints "announce… → complete on the same line",
  with one dot per fetched comment page. `scraper.NullProgress` is the no-op sink
  used by library callers and tests.

## Spec-driven development

The repo keeps a living spec under `specs/`, operated on by the `/spec:*` skills:

- `specs/system/` — the current system: `domain.md` (vocabulary),
  `architecture.md` (modules + flows), `functional.md` (user-facing behavior),
  `observations-web-cadence.md` (the dated live capture the pacing defaults are
  calibrated against — field evidence, not policy).
- `specs/changes/<name>/` — a proposed change: `proposal.md`, `domain.md`,
  `architecture.md`, `plan.md`, and optionally `observations.md`.

When landing a user-visible change, update `specs/system/*` and `README.md`
alongside the code, citing `file:line` the way the existing specs do.

Project-local skills in `.claude/skills/` complement the `/spec:*` ones:

- `/grill-with-docs` — a relentless one-question-at-a-time interview to sharpen a
  plan, writing the glossary and decisions down as they crystallise. Composes
  `grilling` (the interview) with `domain-modeling` (the writing).
- `domain-modeling` — maintains the domain model. **Adapted** from upstream to
  write into `specs/` instead of a root `CONTEXT.md` + `docs/adr/`; there is no
  `CONTEXT.md` and no `docs/adr/` in this repo, and none should be created. See
  `.claude/skills/domain-modeling/UPSTREAM.md` for the provenance and the mapping.

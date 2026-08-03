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
cli.main → auth.get_client → (per URL) url.parse_shortcode → scraper.scrape → writer.write_result
```

Cross-cutting decisions, each of which spans several files:

- **Backend is instagrapi (private mobile API), not instaloader.** Instaloader's
  web-GraphQL post fetch returns empty against current Instagram, so all
  fetching and media download go through an `instagrapi.Client`.
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
- **"Top 10 comments" is a constructed ranking, not Instagram's.**
  `select_top_comments(sort="likes")` = the 10 highest `like_count` among the
  first `--comment-scan-limit` scanned (`0` = all). `sort="instagram"` =
  first-returned order. The rule and scan depth are recorded in every `post.md`
  via `Provenance`.
- **Error handling drives exit codes** (the `cli.main` loop): not-found / private
  → skip that URL; `LoginRequired` / `ChallengeRequired` / `PleaseWaitFewMinutes`
  → fatal, stop the batch; any other exception → skip & continue. Exit `0` = all
  good, `1` = some skipped, `2` = fatal.
- **Progress UI:** `cli.Progress` prints "announce… → complete on the same line",
  with one dot per fetched comment page. `scraper.NullProgress` is the no-op sink
  used by library callers and tests.

## Spec-driven development

The repo keeps a living spec under `specs/`, operated on by the `/spec:*` skills:

- `specs/system/` — the current system: `domain.md` (vocabulary),
  `architecture.md` (modules + flows), `functional.md` (user-facing behavior).
- `specs/changes/<name>/` — a proposed change: `proposal.md`, `domain.md`,
  `architecture.md`, `plan.md`, and optionally `observations.md`.

When landing a user-visible change, update `specs/system/*` and `README.md`
alongside the code, citing `file:line` the way the existing specs do.

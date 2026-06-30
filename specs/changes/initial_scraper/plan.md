# Implementation Plan: Initial Instagram Scraper

> Read `proposal.md`, `domain.md`, and `architecture.md` first.
> Each step is small and ends with a verifiable check. Tests are network-free
> unless marked **(live)**.

## 1. Project scaffolding

- [x] Create `requirements.txt` with `instaloader` (and `pytest` for dev).
- [x] Create the `insta_scraper/` package with an empty `__init__.py`.
- [x] Add `.gitignore` entries: `output/`, `*.session`, session dir, `.env`,
      `__pycache__/`, `.venv/`.
- [x] Create `tests/` directory.
- [x] **Verify**: `pip install -r requirements.txt` succeeds; `python -c "import instaloader"` works.

## 2. URL → shortcode parsing (`url.py`)

- [x] Write `parse_shortcode(url) -> str` handling `/p/`, `/reel/`, `/tv/`,
      with/without trailing slash and query params; raise a clear error on
      unrecognized URLs.
- [x] Write `tests/test_url.py` covering each URL shape + invalid input first.
- [x] **Verify**: `pytest tests/test_url.py` passes (test-first: confirm it
      fails before implementing).

## 3. Internal data model (`models.py`)

- [x] Define `Comment`, `Provenance`, and `ScrapeResult` dataclasses (fields per
      `architecture.md`). No media descriptors — `download_post()` writes media
      and `writer.py` globs the directory.
- [x] **Verify**: importable; fields match the JSON we intend to emit.

## 4. Comment selection logic (`scraper.py`, pure part)

- [x] Write `select_top_comments(comments, n=10, sort="likes")` — for `likes`,
      rank by `likes_count` desc with recency tiebreaker; for `instagram`, keep
      input order. Return first `n`.
- [x] Write `tests/test_comments.py` covering both modes + ties (test-first).
- [x] **Verify**: `pytest tests/test_comments.py` passes.

## 5. Output rendering (`writer.py`, pure part)

- [x] Write `render_markdown(result) -> str`: the `post.md` layout from
      `architecture.md`, including the **provenance header** (fetched-at, tool +
      instaloader version, account, comment-sort rule + scan depth) and an
      **embedded `## Media` section** — images as `![file](file)` inline, videos
      as cover-image embed + `[▶ Play video …](file)` link, every carousel item
      in order.
- [x] Write `render_metadata(result) -> dict` for `metadata.json`, including the
      `provenance` block.
- [x] Write `tests/test_writer.py` over a fixture `ScrapeResult` (test-first);
      assert the provenance header, ranking-caveat line, and `![…](…)` media
      embeds are present.
- [x] **Verify**: `pytest tests/test_writer.py` passes; rendered Markdown looks
      right by eye.

## 6. Authentication (`auth.py`)

- [x] Write `get_instaloader(username=None, session_file=None, load_cookies=None)`:
      if a session file exists, `load_session_from_file()` then `test_login()`;
      reuse only if it passes. Otherwise `--load-cookies` import, else
      `interactive_login(username)` (handles password / 2FA / challenge inline),
      then `save_session_to_file()`.
- [x] Map Instagram auth/challenge exceptions to clear user-facing messages.
- [ ] **Verify (live)**: first run logs in and saves a session; second run loads
      and `test_login`-validates it silently. (Manual — needs real credentials.)

## 7. Scrape + download glue (`scraper.py` / `writer.py`, network parts)

- [x] Configure the `Instaloader` instance: `download_pictures`,
      `download_videos`, `download_video_thumbnails` = true; `download_comments`,
      `save_metadata` off and `post_metadata_txt_pattern=""` (we write our own).
- [x] `scrape(loader, shortcode, sort, scan_limit) -> ScrapeResult`:
      `Post.from_shortcode`, read caption/owner/metadata, scan comments up to
      `scan_limit` (log it), `select_top_comments`, stamp the `Provenance`.
- [x] `write_result(loader, post, result, out_base) -> Path`:
      `download_post(post, target=shortcode)` (downloads **all** media incl.
      carousel nodes + cover), then glob media files and write `post.md` +
      `metadata.json`.
- [ ] **Verify (live)**: one reel **and** one carousel from `SAMPLE_URLS.md` each
      yield a complete folder with every media item present.

## 8. CLI entrypoint (`cli.py` + `__main__.py`)

- [x] `argparse`: positional `url` OR `--file <path>`; flags `--output`,
      `--session-file`, `--load-cookies`, `--delay`, `--comment-sort`
      (`likes`|`instagram`, default `likes`), `--comment-scan-limit` (default
      200, `0` = all).
- [x] Orchestrate: auth → for each URL: parse → scrape → write; batch applies
      `--delay` and treats not-found/private as non-fatal warnings, auth/rate
      errors as fatal (distinct exit codes).
- [x] Wire `python -m insta_scraper` via `__main__.py`.
- [ ] **Verify (live)**: `python -m insta_scraper <reel-url>` end-to-end; then
      `--file SAMPLE_URLS.md` processes several with delays.

## 9. Documentation

- [x] Fill in `README.md`: what it does, install, credentials/env + `--load-cookies`
      setup, usage (single URL + batch), output layout, the comment-ranking
      caveat, and a **ToS / personal-use / EU-GDPR note** (personal archive only;
      the export holds others' personal data — don't republish).
- [x] **Verify**: a fresh reader can install and run from the README alone.

## 10. Final pass

- [x] `pytest` (full suite) green.
- [ ] Manual end-to-end run against 2–3 `SAMPLE_URLS.md` entries; spot-check a
      `post.md`, the comments, and that the video plays.
- [x] Confirm `.gitignore` keeps `output/`, session files, and secrets untracked.

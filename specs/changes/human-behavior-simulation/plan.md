# Implementation Plan: Human-Behavior Simulation

> Read `proposal.md`, `domain.md`, and `architecture.md` first. Steps are
> ordered; each is small and test-first per the repo's conventions. No mocking
> of the code under test; tests stay network-free and sleep-free (injected RNG +
> clock).

## 1. Behavior module — core data & sampling

- [x] Add `instascraper/behavior.py` with `Range` (`lo`, `hi`, `sample(rng)`)
      and the frozen `BehaviorProfile` dataclass (all fields + defaults from
      `architecture.md` §1).
- [x] Add `Humanizer(profile, rng=None, sleep=time.sleep, now=time.monotonic,
      wall=datetime.now)` with `delay(kind)` sampling the right range + optional
      `long_pause`, using the injected `sleep`. `Range.sample` draws floats via
      `uniform`; `Range.sample_int` draws integer counts via `randint` (used for
      `warmup_calls`).
- [x] **Test**: seeded RNG → `delay(kind)` returns values within range for every
      action kind; `long_pause` fires at ~`long_pause_prob` over N draws;
      `sample_int` returns integers within `[lo, hi]`; injected `sleep` records
      durations (never blocks).

## 2. Early give-up (human-scale depth)

- [x] Add `Humanizer.should_stop_early()` → `rng` vs `early_stop_prob`.
- [x] **Test**: `early_stop_prob=0` never stops; `=1` always stops; intermediate
      value stops at the expected rate over N calls with a seeded RNG.

## 3. Rate ceilings & active-hours gating

- [x] Add request-timestamp `deque` + session counters; implement `gate(kind)`
      returning `PROCEED | WAIT(seconds) | STOP` from session ceilings, the
      rolling-`window_seconds` ceiling, and `active_hours` (with edge jitter).
      Policy (`architecture.md` §4): `WAIT` is issued **only** for the rolling
      window (bounded by `window_seconds`); **outside active hours and on
      session/day ceilings, return `STOP`** — never a multi-hour blocking wait.
- [x] Add `record()` to log a request/post against the counters + window.
- [x] **Test**: fake `now()`/`wall` → window ceiling yields a **bounded** `WAIT`;
      session ceiling and outside-active-hours both yield `STOP` (never a
      multi-hour `WAIT`); `active_hours=None` always in-window.

## 4. Politeness backoff

- [x] Add `Humanizer.backoff(attempt)` → jittered `backoff_base * 2**attempt`
      capped at `backoff_max`; helper to know when `backoff_attempts` is
      exhausted.
- [x] **Test**: fake sleep records the schedule; values respect the cap and
      jitter bounds; gives up after `backoff_attempts`.

## 5. Config parsing & profile builder

- [x] Add `build_profile(opts)` parsing `"lo,hi"` range strings and scalars into
      a `BehaviorProfile`; `enabled=False` when humanization is off.
- [x] Add `INSTASCRAPE_HUMANIZE_*` entries to `config.ENV_KEYS`.
- [x] **Test**: `"2,7"` → `Range(2.0, 7.0)`; malformed strings raise a clear
      error; round-trip through save/load config; `--no-humanize` → `enabled
      False`.

## 6. Wire into auth

- [x] In `auth.get_client`, set `client.delay_range` from
      `profile.request_delay` (replace the module constant `DELAY_RANGE` usage);
      accept an optional `humanizer` and call `humanizer.warmup(client)` after a
      fresh login / session load when `warmup_calls > 0`.
- [x] **Test**: `delay_range` reflects the profile; `warmup_calls=0` makes no
      extra calls; existing auth-helper tests still pass.

## 6b. Device-identity continuity (see `architecture.md` §1b; field-evidenced)

> **Amended during implementation (2026-08-03): the default is `android`, not
> `ios`.** instagrapi cannot actually emulate iOS. It speaks Instagram's
> *Android* private API and sends `X-IG-Android-ID`, `X-IG-Capabilities:
> 3brTv10=`, and an Android `bloks_versioning_id` / `version_code` on every
> request (`instagrapi/mixins/private.py:232-240`, `instagrapi/config.py:15-42`);
> only the user-agent string is ours to set. An iPhone UA over that envelope is
> *less* coherent than a plain Android device — the opposite of what this section
> is buying, and exactly the incoherence `domain.md` names as a static flag. So
> `ios` remains selectable and warns; `android` is the default and the coherent
> choice. Confirmed with the user before implementing. Everything else in §6b —
> never re-fingerprinting a live session, never re-logging in speculatively,
> persisting the device — is unaffected and lands in full.

- [x] Add a `device_profile` option (`ios` | `android`, **default `android`** —
      see the amendment above) resolved via the same precedence chain; seed
      instagrapi's device (`set_device` / `set_user_agent`) **only when minting a
      new session** (no session file, or an explicit password/browser login),
      then persist it so it never drifts.
- [x] **Migration — never re-fingerprint a live session** (`architecture.md`
      §1b): on a loaded/reused session, keep the device already in the session;
      do **not** apply `device_profile` to it. If the loaded session's device
      family differs from the configured one, log the one-line "keeping existing
      session; delete `session-<user>.json` to switch (one-time new-device
      prompt)" notice and proceed — no automatic re-login.
- [x] Ensure no code path adds a speculative re-login; a fresh `Client.login` is
      a flag-risk event (a fresh Chrome login already tripped a new-device alert).
- [x] **Test**: a **new** session gets the configured device family and a stable
      UA; a **reused** session keeps its persisted device even when
      `device_profile` differs (and emits the mismatch notice, no re-login);
      default is `android`; `ios` sets the iOS UA *and* warns about the mixed
      envelope.

## 7. Wire into scraper

- [x] `scrape(..., humanizer=None)`; `_scan_comments` calls
      `humanizer.delay("page")` per page and breaks when
      `humanizer.should_stop_early()` is true (before hitting the scan limit).
- [x] Clamp `--comment-scan-limit 0` under humanization to the default depth
      (200) with a one-line notice (`architecture.md` §2b); `--no-humanize` keeps
      `0 = all`. Return the **actual** number of comments scanned so provenance
      can record it (see step 9).
- [x] Ensure `humanizer=None` reproduces today's exhaustive paging exactly (the
      library path).
- [x] **Test**: with a seeded humanizer, paging stops early at the expected rate;
      `scan_limit=0` under humanization scans ≤ 200 (not all); with
      `humanizer=None`, existing `test_comments`/`test_scrape` behavior is
      unchanged.

## 8. Wire into CLI batch loop

- [x] Build the profile from resolved options; construct one `Humanizer`; pass it
      to `get_client` and `scrape`.
- [x] Replace the fixed inter-post `--delay` sleep with `humanizer.delay("post")`;
      call `gate("post")` before each post (`WAIT` → sleep+recheck, `STOP` →
      graceful end with `EXIT_PARTIAL` and a clear message).
- [x] On `PleaseWaitFewMinutes`, use `humanizer.backoff()` retry loop instead of
      immediate `EXIT_FATAL`; keep `LoginRequired`/`ChallengeRequired` fatal.
- [x] Add CLI flags for every parameter + `--no-humanize`. **Do not alias
      `--delay` onto `post_delay`** (`architecture.md` §6): under humanization,
      inter-post pacing is `post_delay` only; `--delay` / `INSTASCRAPE_DELAY`
      feed only the `--no-humanize` fixed-sleep path. When humanization is on and
      `--delay` was passed **explicitly** (`args.delay is not None`), emit a
      one-time deprecation notice pointing at `--humanize-post-delay` and ignore
      it; a `delay` in `.env` from a prior run is ignored silently (no 3 s idle
      surprise on upgrade).
- [x] Update the `cli.py:245` progress banner ("paced ~1–3s/request, Ns between
      posts") so it no longer hardcodes the old pacing under humanization.
- [x] **Test**: `resolve_options` + `build_profile` precedence (CLI > .env > env >
      default); a stale `INSTASCRAPE_DELAY=3` in `.env` does **not** shrink
      `post_delay` under humanization; explicit `--delay` under humanization warns
      and is ignored; `--no-humanize` restores the fixed `--delay` path; backoff
      retry loop exercised with an injected humanizer; `test_cli` updated.

## 9. Provenance

- [x] Record the effective humanization summary (enabled + key ranges, or a short
      hash) in `Provenance` so each `post.md`/`metadata.json` states how it was
      paced. Add `comments_scanned` (the **actual** count paged) alongside the
      configured `comment_scan_limit`, since early-stop/clamp make them differ and
      the top-10 is ranked over the actual set — don't overstate depth.
- [x] **Test**: `render_metadata`/`render_markdown` include the humanization
      summary and `comments_scanned`; when early-stop fires, `comments_scanned <
      comment_scan_limit`; provenance tests updated.

## 10. Docs

- [x] Update `README.md`: new humanization flags, defaults, `--no-humanize`, and
      a short "why this exists" note (behavioral realism ≠ guarantee; ToS caveat
      unchanged).
- [x] Update `specs/system/architecture.md` and `specs/system/domain.md` to add
      the behavior profile / humanizer as first-class components, and extend the
      `PROVENANCE` entity in `specs/system/domain.md` with the humanization
      summary + `comments_scanned` fields.

## 11. Full verification

- [x] Run the whole suite (`pytest`); confirm it is green, network-free, and does
      not actually sleep.
- [ ] Manual smoke on a single **own** post with humanization on: confirm
      variable pacing in the Progress output and that a normal scrape still
      completes.

## Live cadence capture — DONE (see `observations.md`)

- [x] Drove `www.instagram.com` in real Chrome (Playwright MCP), logged in as
      @tillg, viewed reel `DXOCAyzEX8i`; captured the request envelope, endpoint
      mix, and the burst-then-idle timeline. Findings in `observations.md`;
      defaults in `architecture.md` §1 calibrated against it.
- [ ] **Optional re-validation**: periodically re-capture (Instagram changes its
      client) and diff the observed inter-action gaps against the shipped
      defaults; nudge ranges if drift is obvious. Informs defaults only; does not
      change the mobile-API scraping path.

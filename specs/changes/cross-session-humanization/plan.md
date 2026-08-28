# Implementation Plan: Cross-Session Humanization

> Read `proposal.md`, `domain.md`, and `architecture.md` first. Steps are
> ordered; each is small and test-first per the repo's conventions. No mocking of
> the code under test; tests stay network-free and **sleep-free** (injected clock,
> `tmp_path` ledgers — never the real `~/.config`).

## 1. Ledger data & schema

- [x] Add `instascraper/activity.py` with `LEDGER_VERSION = 1` and the `Activity`
      dataclass (all fields from `architecture.md` §1; timestamps are UTC epoch
      seconds).
- [x] Add `Activity.to_dict()` / `from_dict()` with a version check: an unknown
      or missing `version` yields a fresh `Activity`, never an exception.
- [x] **Test**: round-trips through `to_dict`/`from_dict` unchanged; unknown
      version discarded; missing keys fall back to field defaults.

## 2. Ledger I/O — load, prune, atomic save

- [x] Add `ActivityLedger(path, *, window_seconds, lock_timeout=5.0,
      now=time.time, sleep=time.sleep, enabled=True)` (`architecture.md` §1) with
      `load()`, `flush()`, `close()` and an `activity` attribute holding the loaded
      document. Everything `__enter__` needs is a constructor argument — the
      pruning horizon, the lock bound, and **both** time sources. `flush()` writes
      atomically (temp file in the same dir + `os.replace`) and chmods `600`,
      mirroring `auth._dump` (`auth.py:125`).
- [x] Prune on load: drop window entries older than `window_seconds` **and** any
      entry in the future.
- [x] Degrade, never fail: missing / corrupt / truncated / unreadable file, or a
      `last_action` more than `window_seconds` in the future → fresh `Activity`
      plus a one-line warning.
- [x] Generate `salt` once when absent and persist it.
- [x] Add `activity_path(username, override)` (`architecture.md` §4):
      `activity-<username>.json` under the existing `config.CONFIG_DIR`, falling
      back to `activity.json` when no username is configured — the same shape as
      `auth._settings_path`. Root it at `config.CONFIG_DIR`, **not**
      `auth.DEFAULT_SESSION_DIR` (`auth.py:30`): `activity.py` must not import
      `auth`, which would pull instagrapi into the ledger module and invert the
      layering, since `cli` opens the ledger before it authenticates.
- [x] **Test**: write→read preserves counters and window; pruning drops old and
      future entries; each degradation case returns a fresh `Activity` and raises
      nothing; a failed write leaves the previous file intact; saved file is
      `0o600`; `enabled=False` never touches disk.

## 3. Run lock

- [x] Add `__enter__`/`__exit__` acquiring an exclusive `fcntl.flock(LOCK_EX |
      LOCK_NB)`, retrying via the **injected** `sleep` until the constructor's
      `lock_timeout`, then raising a dedicated `LedgerBusy`. Release on `__exit__`
      (the OS also releases on crash).
- [x] Platforms without `flock` → unlocked atomic writes plus a warning, not a
      failure.
- [x] **Test**: a second `ActivityLedger` on the same path raises `LedgerBusy`
      within the timeout — with a recording `sleep` and a fake `now`, so the test
      itself never sleeps the 5 s default (the suite's `conftest` raises on a real
      `time.sleep`); the first releases on `__exit__` so a third succeeds;
      `enabled=False` never locks.

## 4. Wall-clock migration in `behavior.py`

- [x] Change `Humanizer`'s `now` default from `time.monotonic` to `time.time`
      (`behavior.py:122`); the window now holds epoch seconds (`behavior.py:130,
      192`).
- [x] Handle the consequences explicitly (`architecture.md` §2): a backwards
      clock yields a gap of `0`, never a negative wait.
- [x] **Test**: the existing 47 `test_behavior.py` tests still pass unchanged
      (they inject `now`, so this is a default-only change); a backwards clock
      produces a `0` gap.

## 5. Ledger-seeded Humanizer & activity sessions

- [x] `Humanizer(profile, …, ledger=None)`: hold the **ledger** (not a detached
      `Activity`) as `self._ledger`, alias `self._activity = ledger.activity`, and
      seed `requests`/`posts`/`_window` from it — one document, mutated in place, so
      `record()` can `flush()` (`architecture.md` §1). Keep `ledger=None` behaving
      exactly as today.
- [x] Add `session_idle_reset`, `foreground_idle`, `max_requests_per_day`, and
      `max_posts_per_day` to `BehaviorProfile` with the `architecture.md` §3
      defaults. **Not** `lock_timeout`: it is not pacing policy and it applies to
      `--no-humanize` runs, so it lives on the ledger with an `--activity-*` flag
      (`architecture.md` §3, §8).
- [x] Measure the gap once at construction and read it twice (`architecture.md` §3):
      `is_new_session()` against `session_idle_reset` → zero the session counters;
      `is_cold_open()` against `foreground_idle` → warm-up is allowed (and a fresh
      login is a cold open regardless — step 10).
- [x] `build_profile` enforces `foreground_idle ≤ session_idle_reset`, warning and
      raising `foreground_idle` to the reset if a config inverts them.
- [x] Roll the day counters over at **local** midnight (compare the stored local
      ISO date to today's).
- [x] **Test**: a 90 s gap is neither a new session nor a cold open; a 26 min gap (at
      the defaults) keeps the counters **and** is a cold open — the case the split
      exists for; a 40 min gap is both; a fresh ledger is both; day counters reset
      across a local-midnight boundary but not within a day; an inverted config is
      corrected with a warning.

## 6. Day ceilings in `gate()`

- [x] Extend `gate()` (`behavior.py:194`) with the day checks, ordered active
      hours → day → session → window. Day and session ceilings return `STOP`;
      only the rolling window ever returns a bounded `WAIT`
      (`specs/system/architecture.md` "Pacing" — unchanged policy).
- [x] **Test**: hitting a day ceiling returns `STOP` with a clear reason, never a
      multi-hour `WAIT`; the existing gate tests still pass.

## 7. Owed idle

- [x] Extract `Humanizer.sample_delay(kind)` — today's `delay()` minus the sleep —
      and reimplement `delay()` as `sample_delay()` + `_sleep()`. Pure refactor, no
      behavior change: the RNG draw order is identical.
- [x] Add `Humanizer.owed_idle()` per `architecture.md` §4: `0` when there is no
      `last_action`, otherwise `max(0, sample_delay("post") − elapsed)` — the **same
      distribution** as the inter-post pace, long-pause tail included.
- [x] **Test**: `delay()` sleeps exactly what `sample_delay()` returns for the same
      seed, and every existing think-time test still passes.
- [x] **Test**: with `long_pause_prob = 1.0` and a seeded RNG, `owed_idle()` on a
      fresh-ish gap exceeds `post_delay.hi` — the tail reaches the multi-invocation
      path, which bare `post_delay` sampling could never do.
- [x] **Test**: fresh ledger → `0`; `last_action` 2s ago with a seeded RNG → the
      expected remainder; a long gap → `0`; a backwards clock → `0` and never
      negative; humanization off → `0`.

## 8. Stable daily active-hours edges

- [x] Replace the per-`Humanizer` draws (`behavior.py:135-136`) with the
      `(salt, local date, which)` derivation from `architecture.md` §5. With no
      ledger, fall back to today's RNG draw so nothing regresses.
- [x] **Test**: same salt + date → identical shift across many `Humanizer`
      instances; different date → different shift; shift stays within
      `active_hours_jitter`; the existing
      `test_active_hours_edge_is_jittered_not_a_hard_clock_tick` still holds for
      the no-ledger path.

## 9. Persist on record

- [x] `record()` updates `last_action` and the session/day counters, and flushes
      **after every recorded post** (`architecture.md` §7) so a crash costs at
      most one post's budget.
- [x] `record()` stays **unconditional** — it has never had a `profile.enabled`
      short-circuit (`behavior.py:186-192`), and must not acquire one, because
      accounting is not pacing (`architecture.md` §1). `--no-humanize` stops the
      waiting and the gating; only `--no-activity-ledger` stops the file. Pin it
      with a test so it is not "tidied up" into a short-circuit later.
- [x] **Test**: recording a post updates the on-disk ledger; recording a request
      updates in-memory state without a flush per request; a second `Humanizer`
      built from the same path sees the recorded state.
- [x] **Test (the mixed-workflow trap)**: a humanized run, then an *unhumanized* run
      of N posts, then a humanized run — the third sees the day counter including
      the unhumanized N, and does **not** report a cold open off a stale
      `last_action`. With `--no-activity-ledger` on the middle run it does, which is
      then the user's explicit choice.

## 10. Warm-up only on a cold open, and the validation request counted

- [x] Gate **only** the reused-session `humanizer.warmup(client)` (`auth.py:238`)
      on `humanizer.is_cold_open()`. Leave the two fresh-login sites
      (`auth.py:262, 289`) unconditional: minting a session *is* an app-open, it
      cannot repeat on a tight loop (`auth.py` never logs in speculatively), and a
      login with no surrounding activity is a louder signal than the burst being
      removed (`architecture.md` §9).
- [x] `record("request")` the session-validation `get_timeline_feed`
      (`auth.py:236`) once it succeeds — the run's first request, and today the
      only one no counter sees (`architecture.md` §4).
- [x] **Test**: a run inside `foreground_idle` on a reused session makes no
      warm-up calls (only the session validation `get_timeline_feed`); a cold open
      still warms up — including the mid-range case that is a cold open *without*
      being a new session; a **dead session at a 90 s gap** re-logs in and *does*
      warm up, while a healthy session at the same gap does not
      (`architecture.md` §9); validating a reused session advances the request
      counter and the window by exactly one; the existing `test_auth.py` warm-up
      tests still pass.

## 11. Wire into the CLI

- [x] Open the ledger around the whole run (`with`), so it flushes and unlocks on
      every exit path including the graceful-stop and fatal returns. It opens
      **before `get_client`** and is keyed on the *configured* username via
      `activity.activity_path` (`architecture.md` §4), mirroring
      `auth._settings_path` (`auth.py:123-127`).
- [x] `LedgerBusy` → `EXIT_FATAL` with the "another instascrape is running for
      @user" message.
- [x] **Gate first, then pay**, both before `get_client` (`architecture.md` §4):
      a `gate("request")` so a day ceiling stops the run without spending a request
      on validating a session it will not use, and *then* `owed_idle()` announced
      via `progress.stage` so it never reads as a hang. Not the other way round — a
      run about to be stopped must not sleep up to 210 s to learn it.
- [x] The pre-login gate is **STOP-only**: a `WAIT` (rolling window, up to
      `window_seconds` = 3600) is reported as `EXIT_PARTIAL` with the reason and
      the clearing time, **not** slept through with `cli.resolve_gate`. Nothing has
      been done yet, so stopping costs nothing, and the persisted window makes this
      reachable at startup for the first time (`architecture.md` §4).
- [x] **The `i < len(urls) - 1` guard at `cli.py:552` stays.** Owed idle alone
      closes the skipped-pacing gap; adding a trailing pace would double-count the
      wire gap the next run already owes, hold the run lock through a pointless
      sleep, and tax the last post of every batch (`architecture.md` §4).
- [x] Add the flags and `ENV_KEYS` entries from `architecture.md` §8 —
      `--no-activity-ledger`, `--activity-file`, `--activity-lock-timeout`, and the
      four `--humanize-*` ones; add `activity_ledger` to `cli._NEVER_SAVED`
      (`cli.py:410`) alongside `humanize`, and only that one: `--activity-file` and
      `--activity-lock-timeout` are locations and timeouts, so they save normally.
- [x] **Test**: `resolve_options` + `build_profile` precedence for each new key;
      `--no-activity-ledger` is honored for the run and **not** written by
      `config_updates`, while `--activity-file` / `--activity-lock-timeout` **are**;
      `LedgerBusy` maps to `EXIT_FATAL`; the ledger is flushed and unlocked on every
      exit path.
- [x] **Test**: a ledger whose window is already full stops the run with
      `EXIT_PARTIAL` **without** logging in and **without** any recorded sleep; a
      run past a day ceiling likewise stops before any owed idle is paid.

## 12. The headline regression test

- [x] **Test**: ten one-URL runs sharing one ledger (fake clock, seeded RNG,
      recording sleep) are **structurally indistinguishable** from one ten-URL batch
      — the inverse of the measurement in `proposal.md`:
      ```
      before:  10 separate runs → 0 paced gaps,  post-counter=[1]*10
      after:   10 separate runs → 9 paced gaps,  post-counter=10
      ```
      Assert, exactly: **nine** post-scale idles (the batch's count — the first run
      owes nothing, and no run pays a trailing one); the final post counter is `10`;
      the recorded request count is the batch's **plus nine** — one session
      validation per invocation, the single residual this change cannot remove
      without a daemon (`architecture.md` §4, `proposal.md` "Expected outcome").
      Assert each gap only *within its distribution*:
      `post_delay.lo ≤ gap ≤ post_delay.hi + long_pause.hi`.
- [x] **Not** asserted: equality of the idle *sums*. Each run reseeds
      (`behavior.py:126`) and `proposal.md` puts persisting the RNG stream out of
      scope, so the two paths consume the stream in different orders by design. A
      tolerance band would be a magic number wide enough to pass and too wide to
      catch a halved idle; the exact distributional check lives in step 7 instead.
- [x] **Test**: one warm-up across the ten runs, not ten.
- [x] **Test**: no trailing pace — a one-URL run records no post-scale idle *after*
      its post, and the ten-URL batch performs nine, not ten; the lock is released
      without a sleep in between.
- [x] **Test**: the *first request* of runs 2–10 lands after the owed idle, not
      before it — assert the recorded sleep precedes the validation
      `get_timeline_feed`, since paying idle after login would be idle Instagram
      never observes (`architecture.md` §4).

## 13. Provenance

- [x] Add `Humanizer.pacing_summary()` returning
      `profile.summary()` + `" · ledger on"` / `" · ledger off"` (and just
      `"off"` when humanization is off), and have `scraper.scrape` fill
      `Provenance.humanization` from it (`scraper.py:177`, currently
      `humanizer.profile.summary()`). The composition belongs on the `Humanizer`,
      not on `BehaviorProfile.summary()` (`behavior.py:67`): the ledger is a
      collaborator, not profile data, and the frozen dataclass stays pure.
- [x] Update `models.py`'s field comment (`models.py:37`), which currently says
      the value is `BehaviorProfile.summary()`.
- [x] **Test**: the string reflects ledger on/off and stays `"off"` under
      `--no-humanize --no-activity-ledger`; the renderer/provenance tests updated.

## 14. Docs

- [x] `README.md`: replace the "Prefer one batch over many runs" workaround with
      the real behavior; document the ledger location, what it stores (timestamps
      and counters only — **no** URLs or content), how to reset it (delete the
      file), the day ceilings, `--no-activity-ledger`, and the one-run-at-a-time
      rule. State plainly that `--no-humanize` stops the *waiting* but still records
      activity, so later humanized runs are not lied to, and that
      `--no-humanize --no-activity-ledger` is the way to get the pre-humanization
      tool with no file at all. Also state the **one-run-at-a-time consequence**:
      every run takes the lock, `--no-humanize` included, so a second concurrent
      run for the same account exits `2` where it used to work. And mark the two
      day ceilings as **accepted guesses** rather than calibrated defaults — the
      first numbers to revisit once `specs/changes/pacing-log/` reports a real
      per-day rate.
- [x] `specs/system/architecture.md`, `domain.md`, `functional.md`: add the ledger
      as a first-class component, redefine *session* as an activity session, note
      that pacing now starts at the first packet rather than the first post fetch,
      and correct the `functional.md` note that currently says rate state is
      per-process (`functional.md:65-68`). Amend `functional.md:60-64`'s
      "`--no-humanize` restores the old fast behavior" with both narrowings — it
      still records activity, and it still takes the run lock.
- [x] Fix the stale pointer in `behavior.py:39-42`, which still cites
      `specs/changes/human-behavior-simulation/observations.md`; the capture now
      lives at `specs/system/observations-web-cadence.md`. One-line comment change,
      in scope because this step is already rewriting the pacing docs.

## 15. Full verification

- [x] Whole suite green; re-run it under the strict `conftest` that raises on any
      real `time.sleep` or `socket.connect` to confirm it stays sleep-free and
      network-free. *(That `conftest` did not exist — the property was a
      convention, not enforced. Added as `tests/conftest.py`, and its guards are
      verified to bite.)*
- [x] Confirm no test reads or writes the real `~/.config/instascraper/`. *(It
      did, once: the first `main()`-level run wrote a real `activity.json`. The
      same `conftest` now redirects `CONFIG_DIR` / `CONFIG_PATH` /
      `DEFAULT_SESSION_DIR` per module into `tmp_path`.)*
- [x] Manual smoke: two sequential single-URL runs on an **own** post — the second
      should announce an owed idle *before* it logs in, and skip warm-up. Inspect
      `activity-<account>.json` after each: `last_action`, the session counters, and
      the day counters should all have advanced, and the second run's counters
      should continue the first's rather than restart.

      **Done live on 2026-08-28, 17:53–18:27 CEST, at eleven runs rather than two**
      (`SAMPLE_URLS.md`, one invocation per URL, defaults throughout). All eleven
      exited `0`. Nine of the ten follow-up runs announced an owed idle *before*
      the login line — 25, 123, 34, 85, 33, 29, 79, 53, 46 s — and the tenth
      correctly owed nothing, its gap (113 s) already exceeding the sampled pace.
      One draw (123 s) exceeded `post_delay.hi`, so the long-pause tail reached
      the multi-invocation path in the wild, which sampling bare `post_delay`
      could never do. Counters climbed monotonically — posts 1 → 11, requests
      2 → 43, one activity session, one day — and no run was a cold open (inter-run
      gaps 1–11 s against `foreground_idle` 300 s), so warm-up fired once for the
      whole series, at the interactive login. The salt stayed fixed, so the
      active-hours edges were stable across all eleven. Per-post cost under
      humanization: 111–311 s wall, ~3.1 min/post including idle.

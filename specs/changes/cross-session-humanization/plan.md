# Implementation Plan: Cross-Session Humanization

> Read `proposal.md`, `domain.md`, and `architecture.md` first. Steps are
> ordered; each is small and test-first per the repo's conventions. No mocking of
> the code under test; tests stay network-free and **sleep-free** (injected clock,
> `tmp_path` ledgers — never the real `~/.config`).

## 1. Ledger data & schema

- [ ] Add `instascraper/activity.py` with `LEDGER_VERSION = 1` and the `Activity`
      dataclass (all fields from `architecture.md` §1; timestamps are UTC epoch
      seconds).
- [ ] Add `Activity.to_dict()` / `from_dict()` with a version check: an unknown
      or missing `version` yields a fresh `Activity`, never an exception.
- [ ] **Test**: round-trips through `to_dict`/`from_dict` unchanged; unknown
      version discarded; missing keys fall back to field defaults.

## 2. Ledger I/O — load, prune, atomic save

- [ ] Add `ActivityLedger(path, now=time.time, enabled=True)` with `load()`,
      `flush()`, `close()`. `flush()` writes atomically (temp file in the same
      dir + `os.replace`) and chmods `600`, mirroring `auth._dump` (`auth.py:125`).
- [ ] Prune on load: drop window entries older than a passed-in `window_seconds`
      **and** any entry in the future.
- [ ] Degrade, never fail: missing / corrupt / truncated / unreadable file, or a
      `last_action` more than `window_seconds` in the future → fresh `Activity`
      plus a one-line warning.
- [ ] Generate `salt` once when absent and persist it.
- [ ] Add `activity_path(username, override)` (`architecture.md` §4):
      `activity-<username>.json`, falling back to `activity.json` when no username
      is configured — the same shape as `auth._settings_path`.
- [ ] **Test**: write→read preserves counters and window; pruning drops old and
      future entries; each degradation case returns a fresh `Activity` and raises
      nothing; a failed write leaves the previous file intact; saved file is
      `0o600`; `enabled=False` never touches disk.

## 3. Run lock

- [ ] Add `__enter__`/`__exit__` acquiring an exclusive `fcntl.flock(LOCK_EX |
      LOCK_NB)`, retrying until `lock_timeout`, then raising a dedicated
      `LedgerBusy`. Release on `__exit__` (the OS also releases on crash).
- [ ] Platforms without `flock` → unlocked atomic writes plus a warning, not a
      failure.
- [ ] **Test**: a second `ActivityLedger` on the same path raises `LedgerBusy`
      within the timeout; the first releases on `__exit__` so a third succeeds;
      `enabled=False` never locks.

## 4. Wall-clock migration in `behavior.py`

- [ ] Change `Humanizer`'s `now` default from `time.monotonic` to `time.time`
      (`behavior.py:122`); the window now holds epoch seconds (`behavior.py:130,
      192`).
- [ ] Handle the consequences explicitly (`architecture.md` §2): a backwards
      clock yields a gap of `0`, never a negative wait.
- [ ] **Test**: the existing 47 `test_behavior.py` tests still pass unchanged
      (they inject `now`, so this is a default-only change); a backwards clock
      produces a `0` gap.

## 5. Ledger-seeded Humanizer & activity sessions

- [ ] `Humanizer(profile, …, ledger=None)`: seed `requests`/`posts`/`_window`
      from the ledger's `Activity`; keep `ledger=None` behaving exactly as today.
- [ ] Add `session_idle_reset`, `foreground_idle`, `max_requests_per_day`,
      `max_posts_per_day`, and `lock_timeout` to `BehaviorProfile` with the
      `architecture.md` §3 defaults.
- [ ] Measure the gap once at construction and read it twice (`architecture.md` §3):
      `is_new_session()` against `session_idle_reset` → zero the session counters and
      set `session_started_at`; `is_cold_open()` against `foreground_idle` → warm-up
      is allowed.
- [ ] `build_profile` enforces `foreground_idle ≤ session_idle_reset`, warning and
      raising `foreground_idle` to the reset if a config inverts them.
- [ ] Roll the day counters over at **local** midnight (compare the stored local
      ISO date to today's).
- [ ] **Test**: a 90 s gap is neither a new session nor a cold open; a 26 min gap (at
      the defaults) keeps the counters **and** is a cold open — the case the split
      exists for; a 40 min gap is both; a fresh ledger is both; day counters reset
      across a local-midnight boundary but not within a day; an inverted config is
      corrected with a warning.

## 6. Day ceilings in `gate()`

- [ ] Extend `gate()` (`behavior.py:194`) with the day checks, ordered active
      hours → day → session → window. Day and session ceilings return `STOP`;
      only the rolling window ever returns a bounded `WAIT`
      (`specs/system/architecture.md` "Pacing" — unchanged policy).
- [ ] **Test**: hitting a day ceiling returns `STOP` with a clear reason, never a
      multi-hour `WAIT`; the existing gate tests still pass.

## 7. Owed idle

- [ ] Extract `Humanizer.sample_delay(kind)` — today's `delay()` minus the sleep —
      and reimplement `delay()` as `sample_delay()` + `_sleep()`. Pure refactor, no
      behavior change: the RNG draw order is identical.
- [ ] Add `Humanizer.owed_idle()` per `architecture.md` §4: `0` when there is no
      `last_action`, otherwise `max(0, sample_delay("post") − elapsed)` — the **same
      distribution** as the inter-post pace, long-pause tail included.
- [ ] **Test**: `delay()` sleeps exactly what `sample_delay()` returns for the same
      seed, and every existing think-time test still passes.
- [ ] **Test**: with `long_pause_prob = 1.0` and a seeded RNG, `owed_idle()` on a
      fresh-ish gap exceeds `post_delay.hi` — the tail reaches the multi-invocation
      path, which bare `post_delay` sampling could never do.
- [ ] **Test**: fresh ledger → `0`; `last_action` 2s ago with a seeded RNG → the
      expected remainder; a long gap → `0`; a backwards clock → `0` and never
      negative; humanization off → `0`.

## 8. Stable daily active-hours edges

- [ ] Replace the per-`Humanizer` draws (`behavior.py:135-136`) with the
      `(salt, local date, which)` derivation from `architecture.md` §5. With no
      ledger, fall back to today's RNG draw so nothing regresses.
- [ ] **Test**: same salt + date → identical shift across many `Humanizer`
      instances; different date → different shift; shift stays within
      `active_hours_jitter`; the existing
      `test_active_hours_edge_is_jittered_not_a_hard_clock_tick` still holds for
      the no-ledger path.

## 9. Persist on record

- [ ] `record()` updates `last_action` and the session/day counters, and flushes
      **after every recorded post** (`architecture.md` §7) so a crash costs at
      most one post's budget.
- [ ] `record()` is **unconditional** — it has no `profile.enabled` short-circuit,
      because accounting is not pacing (`architecture.md` §1). `--no-humanize` stops
      the waiting and the gating; only `--no-activity-ledger` stops the file.
- [ ] **Test**: recording a post updates the on-disk ledger; recording a request
      updates in-memory state without a flush per request; a second `Humanizer`
      built from the same path sees the recorded state.
- [ ] **Test (the mixed-workflow trap)**: a humanized run, then an *unhumanized* run
      of N posts, then a humanized run — the third sees the day counter including
      the unhumanized N, and does **not** report a cold open off a stale
      `last_action`. With `--no-activity-ledger` on the middle run it does, which is
      then the user's explicit choice.

## 10. Warm-up only on a cold open, and the validation request counted

- [ ] Gate the three `humanizer.warmup(client)` call sites (`auth.py:238, 262,
      289`) on `humanizer.is_cold_open()`.
- [ ] `record("request")` the session-validation `get_timeline_feed`
      (`auth.py:236`) once it succeeds — the run's first request, and today the
      only one no counter sees (`architecture.md` §4).
- [ ] **Test**: a run inside `foreground_idle` makes no warm-up calls (only the
      session validation `get_timeline_feed`); a cold open still warms up — including
      the mid-range case that is a cold open *without* being a new session;
      validating a reused session advances the request counter and the window by
      exactly one; the existing `test_auth.py` warm-up tests still pass.

## 11. Wire into the CLI

- [ ] Open the ledger around the whole run (`with`), so it flushes and unlocks on
      every exit path including the graceful-stop and fatal returns. It opens
      **before `get_client`** and is keyed on the *configured* username via
      `activity.activity_path` (`architecture.md` §4), mirroring
      `auth._settings_path` (`auth.py:123-127`).
- [ ] `LedgerBusy` → `EXIT_FATAL` with the "another instascrape is running for
      @user" message.
- [ ] Pay `owed_idle()` once **before `get_client`** — the session-validation
      request is the run's first packet — announced via `progress.stage` so it
      never reads as a hang. Then `gate("request")` before login, so a day ceiling
      stops the run without spending a request on it. Together with pacing the
      last post, this closes the skipped-pacing gap at `cli.py:552`.
- [ ] Add the flags and `ENV_KEYS` entries from `architecture.md` §8; add
      `activity_ledger` to `cli._NEVER_SAVED` (`cli.py:410`) alongside `humanize`.
- [ ] **Test**: `resolve_options` + `build_profile` precedence for each new key;
      `--no-activity-ledger` is honored for the run and **not** written by
      `config_updates`; `LedgerBusy` maps to `EXIT_FATAL`; the ledger is flushed and
      unlocked on every exit path.

## 12. The headline regression test

- [ ] **Test**: ten one-URL runs sharing one ledger (fake clock, seeded RNG,
      recording sleep) are **structurally indistinguishable** from one ten-URL batch
      — the inverse of the measurement in `proposal.md`:
      ```
      before:  10 separate runs → 0 paced gaps,  post-counter=[1]*10
      after:   10 separate runs → 9 paced gaps,  post-counter=10
      ```
      Assert, exactly: **nine** post-scale idles (the batch's count — the first run
      owes nothing); the final post counter is `10`; the recorded request count
      matches the batch's. Assert each gap only *within its distribution*:
      `post_delay.lo ≤ gap ≤ post_delay.hi + long_pause.hi`.
- [ ] **Not** asserted: equality of the idle *sums*. Each run reseeds
      (`behavior.py:126`) and `proposal.md` puts persisting the RNG stream out of
      scope, so the two paths consume the stream in different orders by design. A
      tolerance band would be a magic number wide enough to pass and too wide to
      catch a halved idle; the exact distributional check lives in step 7 instead.
- [ ] **Test**: one warm-up across the ten runs, not ten.
- [ ] **Test**: the *first request* of runs 2–10 lands after the owed idle, not
      before it — assert the recorded sleep precedes the validation
      `get_timeline_feed`, since paying idle after login would be idle Instagram
      never observes (`architecture.md` §4).

## 13. Provenance

- [ ] Note cross-session state in the humanization summary (e.g. `… · ledger on`)
      so `post.md` stops implying full pacing for loop-driven runs that had it
      disabled.
- [ ] **Test**: the summary reflects ledger on/off; provenance tests updated.

## 14. Docs

- [ ] `README.md`: replace the "Prefer one batch over many runs" workaround with
      the real behavior; document the ledger location, what it stores (timestamps
      and counters only — **no** URLs or content), how to reset it (delete the
      file), the day ceilings, `--no-activity-ledger`, and the one-run-at-a-time
      rule. State plainly that `--no-humanize` stops the *waiting* but still records
      activity, so later humanized runs are not lied to, and that
      `--no-humanize --no-activity-ledger` is the way to get the pre-humanization
      tool with no file at all.
- [ ] `specs/system/architecture.md`, `domain.md`, `functional.md`: add the ledger
      as a first-class component, redefine *session* as an activity session, note
      that pacing now starts at the first packet rather than the first post fetch,
      and correct the `functional.md` note that currently says rate state is
      per-process.

## 15. Full verification

- [ ] Whole suite green; re-run it under the strict `conftest` that raises on any
      real `time.sleep` or `socket.connect` to confirm it stays sleep-free and
      network-free.
- [ ] Confirm no test reads or writes the real `~/.config/instascraper/`.
- [ ] Manual smoke: two sequential single-URL runs on an **own** post — the second
      should announce an owed idle *before* it logs in, and skip warm-up. Inspect
      `activity-<account>.json` after each: `last_action`, the session counters, and
      the day counters should all have advanced, and the second run's counters
      should continue the first's rather than restart.

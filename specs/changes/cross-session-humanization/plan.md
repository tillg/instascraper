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
- [ ] Add `session_idle_reset`, `max_requests_per_day`, `max_posts_per_day`, and
      `lock_timeout` to `BehaviorProfile` with the `architecture.md` §3 defaults.
- [ ] Compute cold-open vs continuation at construction: gap > reset → zero the
      session counters, set `session_started_at`, `is_cold_open()` true; gap ≤
      reset → keep them, `is_cold_open()` false.
- [ ] Roll the day counters over at **local** midnight (compare the stored local
      ISO date to today's).
- [ ] **Test**: gap < reset keeps counters and is not a cold open; gap > reset
      zeroes them and is a cold open; a fresh ledger is a cold open; day counters
      reset across a local-midnight boundary but not within a day.

## 6. Day ceilings in `gate()`

- [ ] Extend `gate()` (`behavior.py:194`) with the day checks, ordered active
      hours → day → session → window. Day and session ceilings return `STOP`;
      only the rolling window ever returns a bounded `WAIT`
      (`specs/system/architecture.md` "Pacing" — unchanged policy).
- [ ] **Test**: hitting a day ceiling returns `STOP` with a clear reason, never a
      multi-hour `WAIT`; the existing gate tests still pass.

## 7. Owed idle

- [ ] Add `Humanizer.owed_idle()` per `architecture.md` §4: `0` when there is no
      `last_action`, otherwise `max(0, sampled post_delay − elapsed)`.
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
- [ ] **Test**: recording a post updates the on-disk ledger; recording a request
      updates in-memory state without a flush per request; a second `Humanizer`
      built from the same path sees the recorded state.

## 10. Warm-up only on a cold open

- [ ] Gate the three `humanizer.warmup(client)` call sites (`auth.py:233, 257,
      284`) on `humanizer.is_cold_open()`.
- [ ] **Test**: a continuation run makes no warm-up calls (only the session
      validation `get_timeline_feed`); a cold open still warms up; the existing
      `test_auth.py` warm-up tests still pass.

## 11. Wire into the CLI

- [ ] Open the ledger around the whole run (`with`), so it flushes and unlocks on
      every exit path including the graceful-stop and fatal returns.
- [ ] `LedgerBusy` → `EXIT_FATAL` with the "another instascrape is running for
      @user" message.
- [ ] Pay `owed_idle()` once after login and before the loop, announced via
      `progress.stage` so it never reads as a hang. This is the fix for the
      skipped-pacing gap at `cli.py:552`.
- [ ] Add the flags and `ENV_KEYS` entries from `architecture.md` §8; add
      `activity_ledger` to `cli._NEVER_SAVED` (`cli.py:410`) alongside `humanize`.
- [ ] **Test**: `resolve_options` + `build_profile` precedence for each new key;
      `--no-activity-ledger` is honored for the run and **not** written by
      `config_updates`; `LedgerBusy` maps to `EXIT_FATAL`.

## 12. The headline regression test

- [ ] **Test**: ten one-URL runs sharing one ledger (fake clock, seeded RNG,
      recording sleep) produce total idle and final counters **matching** one
      ten-URL batch — the inverse of the measurement in `proposal.md`:
      ```
      before:  10 separate runs → idle=0s, post-counter=[1]*10
      after:   10 separate runs → idle≈batch idle, post-counter=10
      ```
- [ ] **Test**: one warm-up across the ten runs, not ten.

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
      rule.
- [ ] `specs/system/architecture.md`, `domain.md`, `functional.md`: add the ledger
      as a first-class component, redefine *session* as an activity session, and
      correct the `functional.md` note that currently says rate state is
      per-process.

## 15. Full verification

- [ ] Whole suite green; re-run it under the strict `conftest` that raises on any
      real `time.sleep` or `socket.connect` to confirm it stays sleep-free and
      network-free.
- [ ] Confirm no test reads or writes the real `~/.config/instascraper/`.
- [ ] Manual smoke: two sequential single-URL runs on an **own** post — the second
      should announce an owed idle and skip warm-up.

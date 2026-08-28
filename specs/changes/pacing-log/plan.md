# Implementation Plan: Pacing Log

> Read `proposal.md`, `domain.md`, and `architecture.md` first, and land
> `specs/changes/cross-session-humanization/` before starting: steps 3–5 emit
> events (`owed_idle`, `warmup` skipped, day-ceiling `gate`) that do not exist
> until it does.
>
> Steps are ordered; each is small and test-first per the repo's conventions.
> No mocking of the code under test; tests stay network-free and **sleep-free**
> (injected clock and `run_id`, `tmp_path` logs — never the real `~/.config`).

## 1. The sink

- [ ] Add `NullPacingLog` (no-op `event(name, **fields)` + `close()`) and
      `PacingLog(path, now=time.time, run_id=…, max_bytes=5_000_000,
      enabled=True)` to `activity.py`, per `architecture.md` §2. `event()` writes
      one JSON line — `{"t": …, "ev": …, "run": …, **fields}` — and flushes; the
      file is opened `"a"` and chmod `600`.
- [ ] **Test**: every emitted line parses as JSON and carries `t`/`ev`/`run`;
      injected clock + `run_id` make the trail assertable byte-for-byte;
      `enabled=False` and `NullPacingLog` never touch disk; the saved file is
      `0o600`.

## 2. Bounded and unbreakable

- [ ] Rotate **at open only**: if the existing file exceeds `max_bytes`,
      `os.replace` it to `<name>.1` (exactly one generation kept).
- [ ] Never fatal: any `OSError` on open or write warns **once**, then the
      instance degrades to a null sink for the rest of the run.
- [ ] **Test**: rotation fires above the cap and leaves exactly one `.1`; a write
      that raises `OSError` warns once, swallows the rest, and does not propagate.

## 3. Events from `behavior.py`

- [ ] `Humanizer(…, log=NullPacingLog())` — an injected sink, so `behavior.py`
      keeps zero file handles. Emit `gate`, `think`, `record`, and `pace` from the
      methods that make those decisions, with the payloads in `architecture.md` §3.
- [ ] **Test**: a scripted run through a list-collecting fake sink produces the
      expected event sequence and payloads; the default sink emits nothing.
- [ ] **Test (the central claim)**: the pacing is bit-identical with the log
      absent, working, and broken — assert equal totals **and** equal sleep
      sequences across all three.
- [ ] **Test (privacy guard)**: scan every emitted payload for the fixture's
      shortcode, URL, caption, and commenter handle — none may appear.

## 4. Events from `auth.py`

- [ ] Each warm-up call site emits `warmup` with `fired` and `why`
      (`cold_open` / `continuation` / `disabled`).
- [ ] **Test**: a cold open logs `fired: true`; a continuation logs `fired: false,
      why: "continuation"`; humanization off logs `why: "disabled"`.

## 5. Events from `cli.py`, and the flags

- [ ] Open the `PacingLog` for the whole run; emit `run_start` after the ledger
      loads, `owed_idle` around the wait, `backoff` from the
      `PleaseWaitFewMinutes` handler (`cli.py:368`), and `run_end` on **every**
      exit path (success, graceful stop, fatal, `LedgerBusy`) before closing.
- [ ] Add the flags and `ENV_KEYS` entries from `architecture.md` §6.
      `activity_log` is **not** in `cli._NEVER_SAVED` — it saves like every other
      option, unlike `humanize` and `activity_ledger`.
- [ ] **Test**: `resolve_options` precedence for each new key; each exit path
      yields exactly one `run_start` and one `run_end`; `--no-activity-log` **is**
      written by `config_updates`.

## 6. Docs

- [ ] `README.md`: a **pacing log** section — location, the nine event kinds, the
      byte cap and rotation, `--no-activity-log` / `--activity-log-file` /
      `--activity-log-max-bytes`, that it is never read back and is safe to delete,
      and two or three `jq` one-liners for the questions it exists to answer (idle
      per day, whether any ceiling ever bound, pacing preceding each `backoff`).
- [ ] `specs/system/architecture.md`, `domain.md`: add the pacing log as a
      first-class component beside the ledger.
- [ ] `specs/system/observations-web-cadence.md`: note that the pacing log is now
      the source for *our own* cadence, so future recalibration has field evidence
      from this tool rather than only the dated third-party capture.

## 7. Full verification

- [ ] Whole suite green, still network-free and sleep-free.
- [ ] Confirm no test reads or writes the real `~/.config/instascraper/`.
- [ ] Manual smoke: after two sequential single-URL runs,
      `activity-<account>.jsonl` shows two `run` groups, the second with
      `cold_open:false`, a non-zero `owed_idle`, and `warmup fired:false` — the
      cross-session change visible in one `tail`.
- [ ] Check the byte-cap arithmetic against the real event rate from that smoke
      run; correct the default or the README estimate if ~150 B/event is wrong.

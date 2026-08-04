# Observations: live traffic capture (web client, authenticated)

> **Why this file is in `specs/system/`.** It is the empirical basis for every
> default in `behavior.BehaviorProfile` and for the device decision in
> `auth.py` — a one-off, dated field measurement that cannot be re-derived from
> the code. `architecture.md` ("Pacing") states the conclusions; this keeps the
> raw evidence. Re-capture periodically and diff against the shipped defaults;
> Instagram changes its client.

Captured 2026-07-30 by driving `www.instagram.com` in a real Chrome (Playwright
MCP), logged in as **@tillg**, viewing reel `DXOCAyzEX8i`. Purpose: ground the
`BehaviorProfile` defaults in what a genuine client actually emits. Raw artifacts
in `.playwright-mcp/` (git-ignored).

> Caveat that shapes how to read this: the **web** client (captured here) and the
> **mobile private API** (what instagrapi/`instascrape` speaks) are different
> surfaces. Header envelopes and endpoints differ and are **not** meant to be
> copied across. What transfers is the **cadence and shape** of a human session —
> that is what we calibrate against.

## 0. Real-world detection event: new-device login alert (strongest signal)

Within minutes of the capture login, Instagram emailed @tillg:

> **We noticed a new login, tillg** — Mac OS X · Chrome · Munich, Germany · July
> 30 at 4:11 AM (PDT). *If this was you, you won't be able to access certain
> security and account settings for a few days.*

The timestamp matches our Playwright **Chrome** login exactly. What this proves:

- **Detection fired on login/device novelty — before any scraping cadence.** The
  very first, single, human-driven login tripped the alert. No pacing, depth, or
  request-volume behavior was involved yet.
- **The account's device history is iOS + Safari.** @tillg normally uses the
  **Instagram iPhone app** and **Safari on Mac**. A login from a fresh **Chrome**
  browser (and, for the scraper, a fresh **Android** device — instagrapi's
  default) is a new-device fingerprint against that history.
- **Consequence is a soft restriction** ("certain settings locked for a few
  days"), not a block. Confirming "this was me" clears it fastest.

**Implication (ranks above cadence):** the highest-value anti-flag measure is
**device-identity continuity** — log in rarely, keep one stable device, and make
that device *coherent with how the account is actually used*. The scraper already
persists a session with stable UUIDs (good), but it emulates **Android** while
this account lives on **iOS/Safari** — a mismatch worth fixing. Behavioral
cadence (below) only matters *after* you've cleared the device/login gate.

> **Resolution (2026-08-03).** The iOS/Android mismatch turned out to be
> unfixable from this backend, so it was *not* fixed: instagrapi speaks the
> Android private API and sends `X-IG-Android-ID` / `X-IG-Capabilities:
> 3brTv10=` on every request regardless of the user-agent
> (`instagrapi/mixins/private.py:232-240`). An iPhone UA over that envelope is
> *less* coherent than a plain Android device. So `device_profile` defaults to
> `android` — coherent and stable — with `ios` available and warning. The
> durable half of this finding did land: the device is seeded once when a
> session is minted and a reused session is never re-fingerprinted
> (`specs/system/architecture.md`, "Authentication & session flow").

## 1. Request envelope (per GraphQL POST) — the static fingerprint

From a captured `POST /api/graphql` request:

```
x-ig-app-id: 936619743392459          # web app id (mobile app uses a different one)
x-asbd-id: 359341
x-fb-friendly-name: <OperationName>    # every query is named, e.g.
                                       #   QuickPromotionSupportIGSchemaBatchFetchQuery
                                       #   PolarisAPIGetFrCookieQuery
x-fb-lsd, x-csrftoken                  # per-session anti-CSRF tokens
x-ig-max-touch-points, sec-ch-ua*      # client hints, consistent with the UA
user-agent: Mozilla/5.0 (Macintosh; …) Chrome/150 …   # stable across the session
```

Request body carries a large machine-verifiable envelope: `doc_id` (persisted
query id), `fb_dtsg` / `jazoest` / `lsd` tokens, `__spin_t` timestamp, `av`
(actor id), `__csr`, `__hs`, `__rev`, …

**Implication for us:** the tool must present a **coherent** envelope for *its*
client (mobile). instagrapi already does this (stable device UUIDs, matching
UA/app-id). The lesson is *coherence and stability*, not copying web headers.
This is **device identity** — deliberately *not* part of `BehaviorProfile`
(which is all sampled timing), because nothing here may be randomized; it must
stay consistent within and across sessions.

## 2. Endpoint mix — not a single call shape

Three distinct shapes were observed in one post view:

| Shape | Example | Notes |
|-------|---------|-------|
| `POST /api/graphql` | friendly-named Relay queries | most metadata / QP / fraud-cookie calls |
| `POST /graphql/query` | persisted `doc_id` queries | heavier data (feed items, comments) |
| `GET/POST /api/v1/…` | `/api/v1/web/fxcal/ig_sso_users/` | REST endpoints |

**Implication:** a real client interleaves several endpoint families; a scraper
that only ever hits one `media/{id}/comments/` endpoint in a loop is
distinguishable. Partially addressed by **warm-up** (`warmup_calls`) — a few
benign app-open calls at session start. Full mid-batch interleaving of
feed/story/reels-tray calls was deliberately left out of scope: lower value for
the cost, and easy to get wrong.

## 3. Cadence — burst-then-idle (the headline finding)

Timeline of API calls after opening the reel (ms from navigation start,
start-to-start gaps):

```
open post ─┐
  4304  ┐
  4333  │  gaps: 29,4,1 ms      ← ~9 requests fired in a
  4337  │                          ~1.8 s BURST (near-parallel)
  4338  │
  4745  │  407 ms
  4761  │  16 ms
  6055  │  1294 ms
  6057  │  2 ms
  6068  ┘  11 ms
 10509     4441 ms               ← lone follow-up
 ── then SILENCE until user acts ──
 33146     ~22.6 s idle          ← next user action
 90187     ~57 s idle            ← next user action (open comments)
 91102     915 ms  (paged load)
 94104     3002 ms  (paged load)
```

Two structural facts:

- **Actions produce short parallel bursts, not evenly-spaced singles.** One click
  → ~9 requests within ~1.8 s.
- **Bursts are separated by long human idle** — here 4.4 s, then **22 s**, then
  **57 s**. Idle dominates the timeline.

**Implication (this is the crux):** the pre-humanization scraper emitted *serial,
single requests spaced a uniform 1–3 s with no idle gaps* — the exact inverse of
the observed shape. What shipped models **(b)**: substantially larger,
high-variance idle between logical actions (`post_delay = Range(20, 90)` plus
`long_pause`), instead of a flat drip. **(a)**, reproducing the tight
intra-action burst, was *not* attempted — that shape lives inside instagrapi's
per-endpoint fan-out, which the tool does not control; `request_delay` stays a
small uniform pause.

## 4. Comments are lazy + shallow by default

- Logged-in `/reel/<code>/` **redirects to** the full-screen `/reels/<code>/`
  player. Comments are **not** loaded with the post — they load only when the
  user opens the comment panel.
- When opened, comments arrive a **screenful at a time** (paged loads at 915 ms
  and 3002 ms apart in the capture), and only continue if the user keeps
  scrolling.

**Implication:** a human rarely loads *all* comments; they open the panel maybe,
read a screen, and stop. This became `early_stop_prob = 0.3` (a per-page chance
of being the last) plus the `scan_depth_clamp = 200` treatment of
`--comment-scan-limit 0`, which under humanization no longer means "page every
comment".

## 5. Duplicate/paired requests

Comment loads fired as **identical pairs** (same timestamp, twice) — React
strict-mode double-invoke / retry behavior. Real client traffic is not perfectly
deduplicated. Minor, but a perfectly-deduplicated request stream is itself a
faint tell. Not something we need to fake, just noted.

## Summary → parameters

| Observation | What shipped |
|-------------|--------------|
| Burst of ~9 near-parallel calls per action | *not modeled* — lives inside instagrapi's fan-out; out of our control |
| 4–57 s idle between actions, high variance | `post_delay = Range(20, 90)`, `long_pause = Range(30, 120)` @ `long_pause_prob = 0.2` |
| Interleaved endpoint families | `warmup_calls = Range(0, 2)` only; mid-batch interleaving out of scope |
| Comments lazy + read a screenful | `early_stop_prob = 0.3`, `scan_depth_clamp = 200` |
| Stable, coherent envelope | `device_profile` (stable, **not** randomized); reused sessions never re-fingerprinted |
| Per-request pacing exists but is small | `request_delay = Range(1, 4)`, applied via instagrapi's own `delay_range` |
| — (not from this capture) | `max_*_per_session` / `_per_window`, `active_hours`, politeness backoff |

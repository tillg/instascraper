# Decision Record Format

This repo has no `docs/adr/` and no numbered ADR files. Decisions are recorded **in
the architecture spec next to the thing they constrain**, so a reader hits the "why"
while reading the "what". Do not create `docs/adr/`.

## Where a decision goes

| Situation | File | Section |
|-----------|------|---------|
| Decision inside a change in flight | `specs/changes/<name>/architecture.md` | `## Key decisions`, as the next `### N. <Title>` |
| An alternative ruled out during that change | `specs/changes/<name>/architecture.md` | `## Rejected alternatives` |
| Something explicitly *not* being done | `specs/changes/<name>/proposal.md` | `## Scope` → **Out of scope** |
| Decision about the system as it stands | `specs/system/architecture.md` | the relevant section, or `## Tech & key decision` if it's foundational |
| Decision spanning several modules, that a newcomer must know | `CLAUDE.md` | the "Cross-cutting decisions" bullet list |

The last row is this repo's real equivalent of an ADR set: a short list of decisions
that each span several files, written so that violating one is recognisable. Adding
to it is a deliberate act — it's the always-loaded context, so keep it to decisions
that would otherwise be silently regressed.

## Template

A `## Key decisions` entry is a titled section that states the decision, then the
reasoning, then the consequence:

```md
### 7. Flush cadence: after every recorded post

Flushing once at exit loses everything if the process is killed mid-batch — and a
killed batch is exactly when the state matters. Flushing on every `record()` is one
small `os.replace` per request, which is cheap next to a multi-second paced HTTP
call. Compromise, and it is the safe one: **flush after every recorded post** (and
once at exit), so a crash loses at most one post's worth of budget.
```

A rejected alternative is one bolded claim plus the reason, one bullet:

```md
- **Persist counters inside the existing session JSON.** Rejected — that file is
  instagrapi's `dump_settings` format and is the *device identity*, which
  `specs/system/architecture.md` deliberately treats as untouchable.
```

The title states the decision, not the topic. "Flush cadence: after every recorded
post" — not "Flushing". A reader scanning headings should get the answers.

## House style

- **Cite `file:line`** for every claim about current behavior (`behavior.py:122`,
  `cli.py:552`). This is the repo's convention and it is what makes the specs
  checkable rather than decorative.
- **Bold the decision itself** so it survives skimming.
- **Name the trade-off, not just the winner.** "Compromise, and it is the safe one"
  is more useful than a bare assertion.
- **Numbered sections are referenced by number** from `plan.md` (e.g. "per
  `architecture.md` §7"). Inserting a section in the middle means renumbering the
  ones after it *and* fixing those references.
- No `Status:` frontmatter, no `Considered Options` / `Consequences` headings — the
  prose carries them. Add structure only where it earns its place.

## When to record a decision

All three of these must be true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will look at the code and wonder "why on earth did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If a decision is easy to reverse, skip it — you'll just reverse it. If it's not surprising, nobody will wonder why. If there was no real alternative, there's nothing to record beyond "we did the obvious thing."

### What qualifies

- **Architectural shape.** "We're using a monorepo." "The write model is event-sourced, the read model is projected into Postgres."
- **Integration patterns between contexts.** "Ordering and Billing communicate via domain events, not synchronous HTTP."
- **Technology choices that carry lock-in.** Database, message bus, auth provider, deployment target. Not every library — just the ones that would take a quarter to swap out.
- **Boundary and scope decisions.** "Customer data is owned by the Customer context; other contexts reference it by ID only." The explicit no-s are as valuable as the yes-s.
- **Deliberate deviations from the obvious path.** "We're using manual SQL instead of an ORM because X." Anything where a reasonable reader would assume the opposite. These stop the next engineer from "fixing" something that was deliberate.
- **Constraints not visible in the code.** "We can't use AWS because of compliance requirements." "Response times must be under 200ms because of the partner API contract."
- **Rejected alternatives when the rejection is non-obvious.** If you considered GraphQL and picked REST for subtle reasons, record it — otherwise someone will suggest GraphQL again in six months.

### What this repo has learned to record

Real examples of the "deliberate deviation" and "would be silently regressed"
categories, worth pattern-matching against:

- **A banned API that looks like the obvious one.** `media_info` falls back to web
  GraphQL and returns an HTML login wall as a `200`, so only `media_info_v1` is
  allowed — and both test suites booby-trap the banned helpers so it can't silently
  regress. A decision worth recording is often one worth *enforcing in a test*.
- **Two similar options with deliberately opposite defaults.** `--no-humanize` is
  never persisted; `--no-activity-log` is. The contrast *is* the decision, and it
  reads as an inconsistency to anyone who doesn't know why.
- **Values that pull against each other on purpose.** "Identity is stable, behavior
  is varied." Without the record, someone will make them consistent.

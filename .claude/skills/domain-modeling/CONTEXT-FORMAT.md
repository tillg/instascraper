# Glossary Format

The glossary is a two-column Markdown table under `## Glossary`, in
`specs/system/domain.md` (settled system) or `specs/changes/<name>/domain.md`
(change in flight).

## Structure

```md
## Glossary

| Term | Meaning |
|------|---------|
| **Shortcode** | The short id in the URL (e.g. `DXOCAyzEX8i`). Canonical identity; names the output folder. |
| **Top 10 comments** | A **constructed ranking** — the 10 comments with the highest like count among the ones *actually scanned*. **Not** Instagram's opaque in-app "top" order. *Avoid:* "Instagram's top comments". |
```

Bold the term. One or two sentences in the Meaning cell. Where the repo has picked
one word over its synonyms, name the losers inline with `*Avoid:* …` — the table has
no separate column for it, and burying the choice loses it.

## Rules

- **Be opinionated.** When multiple words exist for the same concept, pick the best one and list the others under `*Avoid:*`.
- **Keep definitions tight.** One or two sentences max. Define what it IS, not what it does.
- **Only include terms specific to this project's context.** General programming concepts (timeouts, error types, utility patterns) don't belong even if the project uses them extensively. Before adding a term, ask: is this a concept unique to this context, or a general programming concept? Only the former belongs.
- **No implementation details.** "Sampled from a frozen dataclass of `Range`s" is architecture, not vocabulary. The test: if the definition would change when the code is refactored without changing behavior, it's in the wrong file.
- **Group terms under subheadings** when natural clusters emerge. If all terms belong to a single cohesive area, a flat list is fine.
- **State the constructed ones as constructed.** Where a term names something the repo *invented* rather than something Instagram exposes, say so in the definition — that distinction is the most expensive one to lose.

## Settled system vs change in flight

**`specs/system/domain.md`** — the vocabulary of the system as it exists. Also holds
`## Core entities` (a Mermaid `erDiagram` of the data contract) and
`## Actors & key rules`. Add a row here only for the system as it is *today*.

**`specs/changes/<name>/domain.md`** — the delta. Opens by naming what it extends,
then two tables:

```md
## Glossary

| Term | Meaning |
|------|---------|
| **Activity ledger** | The small JSON document persisting pacing state for one account… |

## Refined terms

| Term | Was | Becomes |
|------|-----|---------|
| **Session** | One `instascrape` process | One **activity session** — bounded by `session_idle_reset` |
```

`## Refined terms` is the important one: it makes a changed meaning visible instead
of overwriting the old definition, so a reader can see the migration rather than
being quietly contradicted by the code.

A change's `domain.md` may also carry `## Processes` (Mermaid flowcharts of the new
decision flow) and `## Actors & key rules` for rules the change introduces.

## When the change lands

Fold the change's `## Glossary` rows into `specs/system/domain.md`, apply each
`## Refined terms` row to the existing definition there, and update
`specs/system/architecture.md` / `functional.md` alongside — per `CLAUDE.md`, the
system spec and `README.md` move with the code, in the same change.

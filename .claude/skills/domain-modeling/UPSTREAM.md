# Upstream provenance

These skills come from
[mattpocock/skills](https://github.com/mattpocock/skills) (`skills/engineering/grill-with-docs`,
`skills/productivity/grilling`, `skills/engineering/domain-modeling`), fetched
2026-08-05. The `agents/openai.yaml` files were not installed — Claude Code only.

**Verbatim:** `grill-with-docs/SKILL.md`, `grilling/SKILL.md`.

**Adapted:** `domain-modeling/SKILL.md`, `CONTEXT-FORMAT.md`, `ADR-FORMAT.md`.
Upstream targets a root `CONTEXT.md` + `CONTEXT-MAP.md` and numbered ADRs in
`docs/adr/`. This repo already files both of those under `specs/` (see `CLAUDE.md`
→ "Spec-driven development"), so leaving it verbatim would have created a second,
competing glossary and a third home for decisions. The mapping applied:

| Upstream | Here |
|----------|------|
| root `CONTEXT.md` | `specs/system/domain.md` → `## Glossary` |
| `CONTEXT-MAP.md` / multi-context | *no equivalent* — replaced by settled-system vs change-in-flight (`specs/system/` vs `specs/changes/<name>/`) |
| `docs/adr/NNNN-slug.md` | `specs/changes/<name>/architecture.md` → `## Key decisions` / `## Rejected alternatives`; `specs/system/architecture.md`; `CLAUDE.md` cross-cutting list |

The *behavior* is unchanged — challenge terms against the glossary, sharpen fuzzy
language, invent edge-case scenarios, cross-reference code, write it down inline,
record decisions only when hard-to-reverse **and** surprising **and** a real
trade-off. Only the filing conventions moved, plus repo-specific house style
(`file:line` citations, `## Refined terms` for changed meanings).

Re-syncing upstream means re-applying that table, not overwriting these files.

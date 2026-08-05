---
name: domain-modeling
description: Build and sharpen a project's domain model. Use when the user wants to pin down domain terminology or a ubiquitous language, record an architectural decision, or when another skill needs to maintain the domain model.
---

# Domain Modeling

Actively build and sharpen the project's domain model as you design. This is the *active* discipline — challenging terms, inventing edge-case scenarios, and writing the glossary and decisions down the moment they crystallise. (Merely *reading* the glossary for vocabulary is not this skill — that's a one-line habit any skill can do. This skill is for when you're changing the model, not just consuming it.)

## File structure

This repo keeps a living spec under `specs/`, and that is where the domain model lives. There is **no** root `CONTEXT.md` and **no** `docs/adr/` — do not create them.

```
specs/
├── system/                  ← the current, settled system
│   ├── domain.md            ← THE GLOSSARY (## Core entities, ## Glossary, ## Actors & key rules)
│   ├── architecture.md      ← settled decisions (## Tech & key decision, ## Components, …)
│   ├── functional.md        ← user-facing behavior
│   └── observations-*.md    ← dated field evidence, not policy
└── changes/<name>/          ← a proposal in flight
    ├── proposal.md          ← problem, proposed change, scope, risks
    ├── domain.md            ← ## Glossary (new terms) + ## Refined terms (changed meanings)
    ├── architecture.md      ← ## Key decisions (### 1., ### 2., …) + ## Rejected alternatives
    └── plan.md              ← ordered, test-first steps
```

**Which file gets the write** — the equivalent of the single-vs-multi-context question:

- **A change is in flight** (there's a `specs/changes/<name>/` for what you're discussing) → new terms go in that change's `domain.md`, decisions in its `architecture.md`. `specs/system/*` is only updated when the change *lands*.
- **No change in flight** (correcting or extending the existing system) → write straight to `specs/system/domain.md` / `architecture.md`.
- If it's unclear which applies, ask.

Never create a `specs/changes/<name>/` just to record a term. If the grilling is clearly heading toward a proposal, say so and let the user decide whether to open one.

## During the session

### Challenge against the glossary

When the user uses a term that conflicts with the existing language in `specs/system/domain.md` (or the in-flight change's `domain.md`), call it out immediately, quoting the existing definition. "`domain.md:64` defines **Session** as a persisted instagrapi login, but you seem to mean a sitting bounded by an idle gap — which is it?"

Watch especially for terms the spec has already **refined**: a `## Refined terms` table means that word has two meanings live in the repo at once, and that is exactly where confusion lands.

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'session' — do you mean the persisted login or the activity session? Those are different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees, and cite `file:line` the way the existing specs do. If you find a contradiction, surface it: "`behavior.py:122` keys the window on `time.monotonic()`, but you just said the window survives a process boundary — which is right?"

The spec is the contract, not a summary: a mismatch between spec and code is a finding either way, and it is worth naming which of the two is wrong.

### Update the glossary inline

When a term is resolved, add or amend its row right there. Don't batch these up — capture them as they happen. Use the format in [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md).

The glossary is **totally devoid of implementation details**. It is not a spec, a scratch pad, or a repository for implementation decisions — `architecture.md` and `plan.md` hold those. It is a glossary and nothing else.

When a term's meaning *changes* rather than being newly coined, it belongs in the change's `## Refined terms` table (Term | Was | Becomes), not silently rewritten in place. The old meaning is in readers' heads and in the code; erasing it hides the migration.

### Record decisions sparingly

Only offer to record a decision when all three are true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If any of the three is missing, skip it. Use the format and placement rules in [ADR-FORMAT.md](./ADR-FORMAT.md).

A rejected alternative is worth as much as the accepted one, and this repo files them explicitly (`## Rejected alternatives`). When the user rules something out for a non-obvious reason during the grilling, that is the moment to capture it — otherwise it gets re-proposed in six months.

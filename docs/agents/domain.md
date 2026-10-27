# Domain Docs

## Layout

This repository uses a single-context layout:

- `GLOSSARY.md` at the repository root.
- Architecture decision records in `docs/adr/`.

## Before exploring

Read the root glossary and ADRs relevant to the area being explored.

If these files do not exist, proceed silently. Create them lazily through
the domain-modeling skill when terms or decisions are resolved.

## Vocabulary

Use the glossary's defined terms when naming domain concepts in issues,
proposals, hypotheses and tests.

If a needed concept is missing, reconsider whether it belongs to the
domain or note the gap for domain-modeling.

## ADR conflicts

Explicitly identify any proposal that contradicts an existing ADR,
including the ADR reference and the reason to reconsider its decision.

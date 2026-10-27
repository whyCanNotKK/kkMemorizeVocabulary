# Issue tracker: Local Markdown

Issues and specs live as Markdown files under `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- Specs: `.scratch/<feature-slug>/spec.md`.
- Implementation tickets: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`,
  numbered from `01`, with one file per ticket.
- Record triage state in a `Status:` line near the top of each ticket.
  Use the values in `docs/agents/triage-labels.md`.
- Append comments and conversation history under `## Comments`.

## Publishing and reading

When a skill says "publish to the issue tracker", create the appropriate
spec or ticket file, creating its directory if needed.

When a skill says "fetch the relevant ticket", read the referenced file.
Resolve ticket numbers within the relevant feature directory.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`, containing Notes, Decisions-so-far and Fog.
- Child ticket: `.scratch/<effort>/issues/<NN>-<slug>.md`.
- Record its type with `Type: research`, `prototype`, `grilling` or `task`.
- Wayfinding lifecycle uses `Status: claimed` and `Status: resolved`.
- Record dependencies near the top as `Blocked by: NN, NN`.
  A ticket is unblocked when every listed ticket is resolved.
- Frontier: choose the first unresolved, unblocked, unclaimed ticket by number.
- Claim: set `Status: claimed` and save before starting work.
- Resolve: append the answer under `## Answer`, set `Status: resolved`,
  and add a summary with a relative link to the map's Decisions-so-far.

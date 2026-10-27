# Project guide

kkMemorizeVocabulary is an offline Windows vocabulary notebook built with
Python, PySide6 / Qt Widgets, and SQLite.

## Start here

At the start of each new chat working in this repository, read:

1. [Project design](docs/项目设计说明.md): the authoritative requirements,
   domain concepts, UI behavior, persistence rules, module boundaries
   (section 9), and acceptance criteria.
2. [README](README.md): usage, development setup, isolated data directories,
   test commands, and packaging instructions.

Before implementing new features, update the project design and any affected
database version strategy, as required by its maintenance rules.
For acceptance or release work, also read [Acceptance record](docs/验收记录.md).

## Repository map

- `main.py` and `vocabulary/app.py`: application entry points and lifecycle.
- `vocabulary/domain.py` and `vocabulary/storage.py`: domain objects,
  validation, SQLite persistence, and transactions.
- `vocabulary/window.py`, `table.py`, `widgets.py`, `shortcuts.py`, and
  `theme.py`: UI coordination, table editing, input controls, shortcuts,
  and appearance; all are under `vocabulary/`.
- `tests/`: storage and interface tests; use temporary databases.
- `build.ps1`, `kkMemorizeVocabulary.spec`, and `scripts/verify_release.py`:
  packaging and release verification. `assets/` holds local visual resources.
- `docs/`: project design, acceptance record, and agent configuration.
- `.scratch/<feature>/`: local specs and tickets, created when needed.

Use an isolated `--data-dir` for development, tests, and smoke checks;
keep demonstration data out of the user's default database.

## Agent skills

### Issue tracker

Track issues and specs as local Markdown files under `.scratch/<feature>/`.
Read `docs/agents/issue-tracker.md` when creating or updating specs or tickets.

### Triage labels

Use the five default triage states in local issue files.
Read `docs/agents/triage-labels.md` when evaluating or changing triage state.

### Domain docs

Use a single-context layout: root `GLOSSARY.md` and `docs/adr/`.
Read `docs/agents/domain.md` before exploring domain concepts or decisions.

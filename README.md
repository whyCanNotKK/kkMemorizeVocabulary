# kkMemorizeVocabulary

**English** | [简体中文](README.zh-CN.md)

An offline vocabulary notebook for Windows. Enter English words, organize Chinese definitions, and hide answers for review. Built with Python, PySide6 / Qt Widgets, and SQLite, it stores all entries and settings locally.

[GitHub repository](https://github.com/whyCanNotKK/kkMemorizeVocabulary) · [Releases](https://github.com/whyCanNotKK/kkMemorizeVocabulary/releases) · [Project design (Chinese)](docs/项目设计说明.md) · [Acceptance record (Chinese)](docs/验收记录.md)

## Features

- **Organize vocabulary**: multiple parts of speech and definitions, multiline translations, and related words with their own definitions.
- **Review with hidden answers**: reveal an entire entry with one click, hide entries individually or globally, star important words, and track learned words.
- **Edit quickly**: edit cells directly or load a complete entry into the bottom form; configure shortcuts for common actions.
- **Search and reorder**: search words, related words, and translations; drag entire entries or shuffle the words still being learned.
- **Exchange entries offline**: import and export complete entries as JSON, with side-by-side choices for duplicates.
- **Keep your progress**: saved entries, learning status, order, and shortcuts persist in a local SQLite database.
- **Seventeen-day study**: study up to 30 new words daily in fixed groups, with morning and afternoon completion and scheduled reviews.

Designed for Windows 10/11 x64. Packaged builds include Python and Qt, so users need no Python installation or internet connection to install or run them. Setting up the source environment and building for the first time require internet access. The app currently has a Chinese interface and does not include an online dictionary, automatic translation, pronunciation, accounts, or cloud sync.

## Get started

### Installer or portable build

Check [Releases](https://github.com/whyCanNotKK/kkMemorizeVocabulary/releases) for packaged builds. If no release assets are available, run from source or build the app using the instructions below. GitHub's **Download ZIP** contains source code; generated files under `dist/` are not included in the repository.

| File | Usage |
| --- | --- |
| `kkMemorizeVocabulary-Setup.exe` | Run the installer. The default location is `%LOCALAPPDATA%\Programs\kkMemorizeVocabulary`, with no administrator access required. A Start menu shortcut is created; a desktop shortcut is optional. |
| `kkMemorizeVocabulary-windows-x64.zip` | Extract the entire folder and run `kkMemorizeVocabulary.exe`. Keep the adjacent `_internal` directory. |
| `kkMemorizeVocabulary-Setup.exe.sha256` | Installer SHA256 checksum, which you can compare with `Get-FileHash`. |

**`BuildInstaller.exe` in the repository root is a build tool.** It requires the source code, Python, and a build environment. To use the app, launch the installed application or `kkMemorizeVocabulary.exe` from the portable build.

### Run from source

Install Python 3.12 x64 on Windows x64, then run these commands in PowerShell:

```powershell
git clone https://github.com/whyCanNotKK/kkMemorizeVocabulary.git
cd kkMemorizeVocabulary
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe main.py --data-dir artifacts\development
```

If you have already downloaded the source, start with the environment creation command from the project root. If the `py` launcher is unavailable, use `python -m venv .venv` after confirming that `python` refers to Python 3.12 x64. The lock file includes runtime, testing, and packaging dependencies.

These commands use an isolated development data directory. Once the environment is ready, you can also run `.\start.bat --data-dir artifacts\development`. For everyday use with the default data directory, run:

```powershell
.\.venv\Scripts\python.exe main.py
```

You can also double-click `start.bat`. It launches the app using the existing `.venv` and does not install dependencies automatically.

## Using the app

### Add and edit entries

1. Enter a word, part of speech, and translation in the bottom form, then press **Ctrl+Enter** to save. Each main or related word requires a name and at least one complete definition. Enter inserts a new line in translation fields.
2. Use “增加释义” (Add definition) for another meaning of the same word, or “新增关联词” (Add related word) for an attached word. Definition actions apply to the word containing the current focus. Each word must retain at least one definition.
3. New entries are starred by default; use the star beside the Save button to change this. A saved new entry appears at the end of the words to learn, expands automatically, and scrolls into view.
4. Double-click a name, part of speech, or translation in the table to edit it. Use the save shortcut or leave the editor to commit; Esc cancels. To add or remove related words or definitions, click the pencil to edit the whole entry, then save or cancel.

Main words must be unique after trimming surrounding whitespace and ignoring case. Related words may share names and do not create reciprocal links automatically. Duplicate or invalid entries keep your input; duplicate warnings offer “查看已有词” (View existing word).

The entry form grows with its content, up to half the window's client height, then scrolls internally. The chevron at its top center collapses or expands the form while retaining your draft. Collapsing does not save the draft; unsaved changes still need to be handled before exiting.

### Review and learning status

- The default tab is “待记” (To learn). You can also select “全部” (All) or “已记下” (Learned). Saved entries start with their answers hidden on each launch.
- Click a gray mask to reveal all definitions and related words. **Ctrl+click a translation** to hide the entire group when not editing.
- The global hide toggle at the top right affects all entries, including those outside the current category. Reveal states last only for the current session; newly saved entries still expand automatically.
- Click the circle to mark an entry as learned, or the red minus in the Learned list to move it back. Table stars save immediately. The trash icon deletes the main word and all attached content after confirmation.
- Each category remembers its scroll position during the current session. All start at the top after restarting.

### Search and order

Search matches literal substrings in main words, their translations, related words, and related translations. Matching ignores case and surrounding whitespace in the query. The query remains when switching categories. Matching entries appear as complete groups with all answers temporarily revealed; clearing the search restores their previous reveal states. Individual hiding is disabled during search, while the global toggle can still change the state restored afterward.

Drag the handle on the left of an entry's actions to move its entire group within To learn or Learned. Release to save; Esc or releasing outside the table viewport cancels. All does not support dragging. The shuffle button at the top left of To learn randomizes the order and scrolls to the top on success. **Dragging and shuffling are disabled while the search query is nonempty.** Shuffling also requires at least two words to learn.

Order is saved in the database. New entries and entries whose learning status changes go to the end of their destination category; ordinary edits preserve position. All shows words to learn first, followed by learned words.

### Seventeen-day study

Open “十七天背单词” to load study tasks. On the first morning visit each day, the app draws up to 30 unselected To learn words into permanent groups of up to 10. The batch stays fixed even after restarting; a short or empty batch is not topped up. Older tasks appear first. Group numbers replace stars, and entries only offer a study completion action, with no editing or dragging.

Entering the page or clicking its refresh icon captures the date and period in Beijing time: morning before 15:00, afternoon from 15:00 onward. Actions keep using that captured date and period until you refresh or reenter, including across midnight. Search and the entry form are hidden on this page. Drafts and the form's collapsed state are preserved, and form shortcuts are inactive until returning to another page. Answer masks and the global hide toggle remain available.

Complete each word in both periods. An afternoon completion settles the round only after its morning completion; completing the morning after an earlier afternoon clears that afternoon mark so another afternoon review is required. Missed sessions retain their progress. The stages are days 1, 2, 4, 7 and 15, with successive gaps of 1, 2, 3 and 8 days counted from the actual settled date. Finished words stay excluded from future draws. Study progress does not change the original Learned status.

The import/export icons on this page use a separate study file: UTF-8 JSON with `format: kkMemorizeVocabulary.study17`, `version: 1`. It includes complete entries for every participating word, their progress keyed by normalized word text rather than database IDs, and daily draw dates and the group counter. Future, already reviewed and fully finished words are all included. Exporting does not change progress.

If local progress exists, importing offers “导入单词和进度” (Words and progress), “仅导入单词” (Words only), or Cancel. Words use the existing duplicate comparison dialog and immediate-save behavior. Unresolved comparisons retain the local entries when closed. Progress is then mapped to the final local IDs and replaces all local study records and draw settings in one transaction, preserving file dates and group numbers. Words-only imports retain local progress, including rebinding it when an adopted entry changes ID. If progress restoration fails, it rolls back that phase while retaining words already saved, and you can retry. Canceling or selecting an invalid file makes no changes.

Successful progress restoration reloads the page's date, period and tasks without drawing new words; the next explicit refresh or page visit uses the restored daily markers. Ordinary word JSON remains version 2 and exchanges entries only; the two file formats use separate import paths. Importing over a participating word preserves its progress, and deleting the word deletes it. A database backup preserves all entries, progress and shortcuts. This source update does not rebuild previously packaged releases.

## Default shortcuts

Open “快捷键设置” (Shortcut settings) at the top to customize the first six actions. Changes take effect immediately and are saved in the database. Shortcuts are handled within the app's own window and support both English and Chinese input modes.

| Shortcut | Action |
| --- | --- |
| Ctrl+N | Add a related word and focus its name field |
| Ctrl+M | Remove the last related word |
| Ctrl+J | Add a definition to the currently focused word |
| Ctrl+K | Remove that word's last definition, keeping at least one |
| Ctrl+Enter | Save the whole entry; when editing a table cell, save that cell first |
| Ctrl+I | Toggle the important flag for the main word in the bottom form |
| Tab / Shift+Tab | Move focus forward / backward |
| Enter | Insert a new line in a translation field |
| Space | Open a focused part-of-speech selector, or toggle a focused star |
| 1–9 | Select a part of speech when its selector has focus |
| n / v / a | Select noun / verb / adjective; repeated a presses alternate between adj. and adv. |

The number keys map to: 1 noun, 2 verb, 3 adjective, 4 adverb, 5 preposition, 6 pronoun, 7 conjunction, 8 interjection, and 9 other. Letter keys accept either case and work with the selector open or closed.

## JSON import and export

The import and export buttons sit to the right of the global hide toggle. Export includes **all saved entries matching both the current category and search query**, including results outside the visible viewport. Hidden answers are exported in full. Save draft entries before exporting them.

Exports use UTF-8 JSON and include important flags, learning status, order, all definitions, and related words. Array order and line breaks inside translations are preserved. Shortcuts, internal database IDs, and temporary reveal states are excluded. Exports use version 2; imports accept versions 1 and 2:

```json
{
  "format": "kkMemorizeVocabulary",
  "version": 2,
  "words": [
    {
      "text": "record",
      "important": true,
      "learned": false,
      "sort_order": 1,
      "senses": [{"pos": "n.", "translation": "记录；唱片"}],
      "related": [
        {"text": "recorder", "senses": [{"pos": "n.", "translation": "录音机"}]}
      ]
    }
  ]
}
```

All fields shown are required except `sort_order`. `related` may be empty; `senses` must contain at least one complete definition. Part-of-speech values are `n.`, `v.`, `adj.`, `adv.`, `prep.`, `pron.`, `conj.`, `interj.`, or `其他` (other). `important` and `learned` must be JSON booleans. When present, `sort_order` must be a positive integer, not a boolean. The entire file is validated first; an invalid format or entry prevents any entries from being written.

Import order and duplicate handling:

- If every entry has a sort number, entries are processed in stable ascending order; otherwise, file order is used. The database assigns new sort numbers rather than reusing imported values.
- Nonduplicate entries are imported in one transaction first. All duplicates then appear for comparison. Only main word names are compared, after trimming whitespace and ignoring case; duplicates within the same file are also detected.
- Click the left card to keep the existing entry, or the right card to adopt the complete imported entry and its status. Adopting deletes and recreates the entry in one transaction, with a new ID and a position at the end of its destination category. Each successful choice immediately removes that card pair.
- Use “全部使用左侧 / 右侧” (Use all left / right) to handle the remaining pairs. Individual choices apply in click order; batch choices follow the remaining candidate order. For repeated names, the last adopted entry wins. A failed batch adoption rolls back the entire batch.
- Closing the comparison window keeps unresolved existing entries, newly imported words, and all choices already saved.

## Data, backups, and updates

The default data directory is `%LOCALAPPDATA%\kkMemorizeVocabulary`, separate from the installation directory:

| File | Contents |
| --- | --- |
| `vocabulary.sqlite3` | All entries, important flags, learning status, order, and shortcuts |
| `application.log` and rotated logs | Errors and diagnostic information |

**Back up or restore**: close the app before copying or restoring the database file. While the app is running, `vocabulary.sqlite3-wal` and `vocabulary.sqlite3-shm` may exist; do not move or delete them individually during use. JSON is useful for exchanging entries; a complete backup should use the database to retain shortcut settings.

**Update**: close the app first. For an installed build, run the new installer using the same installation directory. For a portable build, replace the complete application folder. The user data directory is retained, including when uninstalling through Windows Settings → Apps.

**Compatibility**: the current SQLite schema version is 3. Version 1 and 2 databases migrate automatically, preserving entries, settings and category order. Back up before upgrading and continue using the newer app afterward; older apps cannot open v3 databases. JSON remains version 2, with imports accepting versions 1 and 2. Database and application versions are managed separately.

To use another writable data directory, launch with:

```powershell
.\kkMemorizeVocabulary.exe --data-dir "D:\MyVocabulary"
```

Portable builds also use the default user data directory unless `--data-dir` is supplied. Set this explicitly if you want to carry data with the application folder.

## Development and verification

Use `--data-dir artifacts\development` for development as shown above. Automated tests use temporary databases and do not write to the default user data directory:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The source smoke check writes demonstration entries, produces `smoke-result.json` and interface screenshots, then exits automatically. **An explicit isolated directory is required**:

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test --data-dir artifacts\source-smoke
```

Run the same command again to check data and shortcut restoration after restart. Inspect `ok` and the individual checks in the report. Historical results and acceptance details are in the [acceptance record (Chinese)](docs/验收记录.md).

| Path | Responsibility |
| --- | --- |
| `main.py`, `vocabulary/app.py` | Arguments, data directory, logging, and application lifecycle |
| `vocabulary/domain.py`, `vocabulary/storage.py` | Domain objects, validation, SQLite, and transactions |
| `vocabulary/window.py`, `vocabulary/table.py`, `vocabulary/widgets.py` | Window coordination, table editing, and input controls |
| `vocabulary/shortcuts.py`, `vocabulary/native_shortcuts.py`, `vocabulary/theme.py` | Shortcuts, Windows native key handling, and appearance |
| `vocabulary/transfer.py`, `vocabulary/import_dialog.py` | JSON format and duplicate comparison |
| `tests/` | Storage and interface tests |
| `build.ps1`, `installer/`, `scripts/` | Building, installer configuration, and release verification |

The [project design (Chinese)](docs/项目设计说明.md) defines requirements, module boundaries, and data version policies. Update it and any affected data version strategy before implementing new features.

## Build a Windows installer

The build machine needs Windows x64 and Python 3.12 x64. Double-click **`BuildInstaller.exe`** in the repository root, or run this command from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

The launcher invokes the adjacent `build.ps1`, displays progress, and keeps its window open until you press Enter. On success, it opens the output directory with the installer selected. Keep the launcher with the source code; each build packages the current source.

The script creates `.venv` if missing, installs missing locked dependencies, prepares Inno Setup, runs tests, builds with PyInstaller, verifies bundled source and DLLs, and generates the portable ZIP and installer. The first build downloads the pinned Inno Setup 6.7.3, checks its SHA256, and caches a portable compiler in `build\tools`. No global compiler installation is required; later builds can reuse the prepared environment and cache.

Successful builds produce:

| Path | Contents |
| --- | --- |
| `dist\kkMemorizeVocabulary-Setup.exe` | Complete installer that can be distributed on its own |
| `dist\kkMemorizeVocabulary-Setup.exe.sha256` | Installer SHA256 checksum |
| `dist\kkMemorizeVocabulary\` | Complete portable application directory, including `_internal` |
| `dist\kkMemorizeVocabulary-windows-x64.zip` | Portable application archive |
| `build\installer-build.log` | Build log |

Both README language versions are included in the packaged application. The installer version comes from `pyproject.toml`. A failure prints `BUILD FAILED` and returns a nonzero exit code; old files may remain under `dist/`, so their presence alone does not indicate success. Only one build can run at a time.

After editing the launcher source, `scripts\BuildInstaller.cs`, rebuild it with the .NET Framework compiler included with Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-launcher.ps1
```

If a packaged app reports a Qt DLL loading error, check that its complete application directory is present, then rebuild and verify it with the build script. The script isolates dependency search paths and checks source consistency and Windows / ICU dependencies to prevent DLLs from other development tools entering the bundle.

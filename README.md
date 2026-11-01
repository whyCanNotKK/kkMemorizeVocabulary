# kkMemorizeVocabulary

**English** | [简体中文](README.zh-CN.md)

An offline vocabulary notebook for Windows, with grouped definitions, hidden-answer review, and a dedicated **seventeen-day study** page. Built with Python, PySide6 / Qt Widgets, and SQLite. All data stays on your computer.

[Download releases](https://github.com/whyCanNotKK/kkMemorizeVocabulary/releases) · [Project design (Chinese)](docs/项目设计说明.md)

## Features

- **Seventeen-day study**: daily new words, fixed groups, morning and afternoon sessions, and scheduled reviews.
- **Organized vocabulary**: multiple definitions, multiline translations, related words, stars, and To learn / Learned categories.
- **Clearer interface**: a dark theme, aligned definitions with vertical guides, whole-entry selection, and a collapsible entry form.
- **Quick review**: click to reveal answers or copy a word; navigate by keyboard and keep your scroll position when switching tabs.
- **Easy editing and ordering**: edit cells directly, search words and translations, drag entries, or shuffle the To learn list.
- **Local storage and exchange**: saved entries, study progress, order, and custom shortcuts persist locally; JSON import/export includes duplicate comparison.

Supports Windows 10/11 x64. The interface is currently in Chinese. Packaged builds include Python and Qt and run offline.

## Seventeen-day study

1. Add words to **待记** (To learn), then open **十七天背单词** in the morning. The app randomly draws up to **30 new words per day**, in fixed groups of up to **10**, without redrawing or topping up that day's batch.
2. Complete each round in the morning, then again in the afternoon. Stages are **days 1, 2, 4, 7, and 15**; later reviews follow gaps of 1, 2, 3, and 8 days from the actual completed round. Missed sessions retain their progress.
3. With a word selected and the study list focused, press **Enter** to reveal its answers; press again to mark it done and select the next word.

Sessions use **Beijing time (UTC+8), with the afternoon starting at 15:00**. Refresh or reenter the page after a period or date change. Study progress is separate from the ordinary Learned status; finished words are excluded from future draws.

The study page has its own progress import/export, including participating words and their progress. Ordinary word JSON and study files use separate import paths.

## Get started

Download a build from [Releases](https://github.com/whyCanNotKK/kkMemorizeVocabulary/releases), when available:

- **Installer**: run `kkMemorizeVocabulary-Setup.exe`.
- **Portable**: extract `kkMemorizeVocabulary-windows-x64.zip` and run `kkMemorizeVocabulary.exe`. Keep the complete folder, including `_internal`.

GitHub's **Download ZIP** contains source code. The root `BuildInstaller.exe` builds the app from source; it is a developer tool.

### Run from source

Use Windows x64 and Python 3.12 x64. In PowerShell:

```powershell
git clone https://github.com/whyCanNotKK/kkMemorizeVocabulary.git
cd kkMemorizeVocabulary
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe main.py --data-dir artifacts\development
```

This uses an isolated development database. For everyday use with the default data directory, omit `--data-dir` or double-click `start.bat` after setup.

## Common controls

Enter a word, part of speech, and translation in the bottom form, then save with **Ctrl+Enter**. Add more definitions or related words as needed. Double-click a list cell to edit it; click a word's text to copy its full name. The eye button hides or reveals answers in the current tab.

| Key | Action |
| --- | --- |
| W / S or Up / Down | Previous / next main word, with the list focused |
| Space | Hide / reveal the selected entry, outside search |
| Enter | Reveal / complete the selected word on the study page |
| Ctrl+Up / Ctrl+Down | Scroll without moving the selection |
| Ctrl+Enter | Save the entry or current cell edit |
| Ctrl+N / Ctrl+J | Add a related word / definition in the entry form |

Common entry shortcuts can be changed in **设置** (Settings). Search covers main words, related words, and translations; dragging and shuffling are disabled during search.

## Data and backups

The default database is `%LOCALAPPDATA%\kkMemorizeVocabulary\vocabulary.sqlite3`. **Close the app before copying it** for a complete backup of entries, study progress, and settings. Program updates keep this data directory.

Use JSON to exchange entries or study progress. Ordinary tabs export all entries matching the current category and search, including those outside the viewport. Word exports use JSON v2 (imports accept v1/v2); study files use v3 only. Importing study progress replaces existing local study progress; choose **仅导入单词** (Words only) to keep it.

Use `--data-dir <directory>` to store data elsewhere, including with a portable build.

## Development and builds

Tests use temporary databases. The smoke check requires an isolated data directory:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe main.py --smoke-test --data-dir artifacts\source-smoke
```

Double-click **BuildInstaller.exe** or run:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

The build prepares dependencies, runs tests, packages and verifies the app, and writes the installer and portable ZIP to `dist/`. Source setup and the first build require internet access.

See the [project design](docs/项目设计说明.md) for detailed behavior, architecture, and data formats, and the [acceptance record](docs/验收记录.md) for verification history (both in Chinese).

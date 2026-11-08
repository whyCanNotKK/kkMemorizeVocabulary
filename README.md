# kkMemorizeVocabulary

**English** | [简体中文](README.zh-CN.md)

An offline vocabulary notebook for Windows 10/11 x64, with experimental source and unsigned app support for Apple Silicon macOS. It has a Chinese interface. Organize your own words and definitions, hide answers for review, and study with a seventeen-day review schedule. Your data stays on your computer.

## Install

1. Open [Releases](https://github.com/whyCanNotKK/kkMemorizeVocabulary/releases/latest).
2. Download **kkMemorizeVocabulary-Setup.exe** from **Assets**.
3. Run the installer, then open the app from the Start menu or its desktop shortcut.

The installer includes the required runtime; you do not need to install Python separately.

## macOS (Apple Silicon)

The initial macOS build targets Apple Silicon (M1 or later) and is not signed or notarized. After cloning the repository, double-click `start-macos.command` to launch the source version; the first run creates a virtual environment and installs dependencies. Build the app on an Apple Silicon Mac using the [development guide](docs/开发指南.md). macOS stores data and logs in `~/Library/Application Support/kkMemorizeVocabulary` by default.

## Add your vocabulary

**No vocabulary list or dictionary is bundled. You need to enter or import your own words and definitions.**

- **Enter words:** fill in the word, part of speech and definition in the entry form, then save. Optional phonetic transcriptions, multiple definitions and related words are supported.
- **Import words:** use the import button to load a JSON vocabulary file supported by this app. You can export your entries for reuse or backup.

Once words are in **待记** (To learn), open **十七天背单词** to start studying.

For source setup, testing and builds, see the [development guide (Chinese)](docs/开发指南.md).

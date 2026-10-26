"""SQLite aggregate repository. Every write is transactional."""
from pathlib import Path
import sqlite3

from .domain import DEFAULT_SHORTCUTS, DuplicateWord, RelatedWord, Sense, WordEntry, clean_entry


class Repository:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=3)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise RuntimeError("数据库由更高版本程序创建，请使用新版单词本打开。")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS words (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL UNIQUE,
                important INTEGER NOT NULL DEFAULT 1 CHECK(important IN (0,1)),
                learned INTEGER NOT NULL DEFAULT 0 CHECK(learned IN (0,1)),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE IF NOT EXISTS related_words (
                id INTEGER PRIMARY KEY,
                word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                position INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS senses (
                id INTEGER PRIMARY KEY,
                word_id INTEGER REFERENCES words(id) ON DELETE CASCADE,
                related_id INTEGER REFERENCES related_words(id) ON DELETE CASCADE,
                pos TEXT NOT NULL,
                translation TEXT NOT NULL,
                position INTEGER NOT NULL,
                CHECK((word_id IS NOT NULL) != (related_id IS NOT NULL))
            );
            CREATE INDEX IF NOT EXISTS related_owner ON related_words(word_id, position);
            CREATE INDEX IF NOT EXISTS senses_word ON senses(word_id, position);
            CREATE INDEX IF NOT EXISTS senses_related ON senses(related_id, position);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=1;
        """)
        with self.connection:
            self.connection.executemany("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
                                        DEFAULT_SHORTCUTS.items())

    def close(self):
        self.connection.close()

    def _senses(self, column: str, owner_id: int) -> list[Sense]:
        # column is chosen only by repository code, never supplied by the UI.
        return [Sense(row["pos"], row["translation"]) for row in self.connection.execute(
            f"SELECT pos,translation FROM senses WHERE {column}=? ORDER BY position,id", (owner_id,))]

    def get(self, entry_id: int) -> WordEntry:
        row = self.connection.execute("SELECT * FROM words WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise ValueError("该单词已不存在。")
        children = [RelatedWord(child["text"], self._senses("related_id", child["id"]))
                    for child in self.connection.execute(
                        "SELECT * FROM related_words WHERE word_id=? ORDER BY position,id", (entry_id,))]
        return WordEntry(row["text"], self._senses("word_id", entry_id), children,
                         bool(row["important"]), bool(row["learned"]), entry_id)

    def list_entries(self) -> list[WordEntry]:
        return [self.get(row[0]) for row in self.connection.execute("SELECT id FROM words ORDER BY id ASC")]

    def save(self, draft: WordEntry) -> WordEntry:
        entry = clean_entry(draft)
        normalized = entry.text.casefold()
        existing = self.connection.execute("SELECT id FROM words WHERE normalized_text=?", (normalized,)).fetchone()
        if existing and existing[0] != entry.id:
            raise DuplicateWord(existing[0])
        with self.connection:
            if entry.id is None:
                cursor = self.connection.execute(
                    "INSERT INTO words(text,normalized_text,important,learned) VALUES (?,?,?,?)",
                    (entry.text, normalized, entry.important, entry.learned))
                entry.id = cursor.lastrowid
            else:
                cursor = self.connection.execute("""UPDATE words SET text=?,normalized_text=?,important=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                    (entry.text, normalized, entry.important, entry.id))
                if cursor.rowcount != 1:
                    raise ValueError("该单词已不存在，当前输入已保留。")
                self.connection.execute("DELETE FROM senses WHERE word_id=?", (entry.id,))
                self.connection.execute("DELETE FROM related_words WHERE word_id=?", (entry.id,))
            self.connection.executemany(
                "INSERT INTO senses(word_id,pos,translation,position) VALUES (?,?,?,?)",
                [(entry.id, s.pos, s.translation, i) for i, s in enumerate(entry.senses)])
            for i, child in enumerate(entry.related):
                cursor = self.connection.execute(
                    "INSERT INTO related_words(word_id,text,position) VALUES (?,?,?)", (entry.id, child.text, i))
                self.connection.executemany(
                    "INSERT INTO senses(related_id,pos,translation,position) VALUES (?,?,?,?)",
                    [(cursor.lastrowid, s.pos, s.translation, j) for j, s in enumerate(child.senses)])
        return self.get(entry.id)

    def delete(self, entry_id: int):
        with self.connection:
            self.connection.execute("DELETE FROM words WHERE id=?", (entry_id,))

    def set_flag(self, entry_id: int, flag: str, value: bool):
        if flag not in {"important", "learned"}:
            raise ValueError("不支持的属性")
        with self.connection:
            self.connection.execute(
                f"UPDATE words SET {flag}=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (bool(value), entry_id))

    def shortcuts(self) -> dict[str, str]:
        return {row[0]: row[1] for row in self.connection.execute("SELECT key,value FROM settings")
                if row[0] in DEFAULT_SHORTCUTS}

    def write_shortcuts(self, values: dict[str, str]):
        with self.connection:
            self.connection.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", values.items())


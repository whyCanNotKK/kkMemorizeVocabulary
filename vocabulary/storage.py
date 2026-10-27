"""SQLite aggregate repository. Every write is transactional."""
from pathlib import Path
from contextlib import contextmanager
import json
import random
import sqlite3

from .domain import DEFAULT_SHORTCUTS, DuplicateWord, RelatedWord, Sense, WordEntry, clean_entry
from .transfer import ImportConflict, ImportResult


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
        if version > 2:
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
        """)
        if version < 2:
            with self.connection:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    "ALTER TABLE words ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 1 CHECK(sort_order>0)")
                self.connection.execute("""WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (PARTITION BY learned ORDER BY id) AS position FROM words
                ) UPDATE words SET sort_order=(SELECT position FROM ranked WHERE ranked.id=words.id)""")
                self.connection.execute("PRAGMA user_version=2")
        self.connection.execute("CREATE INDEX IF NOT EXISTS words_order ON words(learned,sort_order,id)")
        maxima = dict(self.connection.execute(
            "SELECT learned,MAX(sort_order) FROM words GROUP BY learned").fetchall())
        self.max_pending_sort_order = maxima.get(0, 0)
        self.max_learned_sort_order = maxima.get(1, 0)
        with self.connection:
            self.connection.executemany("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
                                        DEFAULT_SHORTCUTS.items())

    def close(self):
        self.connection.close()

    @contextmanager
    def _transaction(self):
        """Allocate locally; publish high-water marks only after a successful commit."""
        counters = [self.max_pending_sort_order, self.max_learned_sort_order]
        with self.connection:
            yield counters
        self.max_pending_sort_order, self.max_learned_sort_order = counters

    @staticmethod
    def _next_order(counters, learned):
        counters[int(learned)] += 1
        if counters[int(learned)] > 9223372036854775807:
            raise ValueError("排序号超出 SQLite 整数范围。")
        return counters[int(learned)]

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
                         bool(row["important"]), bool(row["learned"]), entry_id, row["sort_order"])

    def list_entries(self) -> list[WordEntry]:
        return [self.get(row[0]) for row in self.connection.execute(
            "SELECT id FROM words ORDER BY learned,sort_order,id")]

    def save(self, draft: WordEntry) -> WordEntry:
        entry = clean_entry(draft)
        with self._transaction() as counters:
            self._save_entry(entry, counters)
        return self.get(entry.id)

    def _save_entry(self, entry: WordEntry, counters, forced_id=None):
        """Write within the caller's transaction; ordinary edits keep learned state."""
        normalized = entry.text.casefold()
        existing = self.connection.execute("SELECT id FROM words WHERE normalized_text=?", (normalized,)).fetchone()
        if existing and existing[0] != entry.id:
            raise DuplicateWord(existing[0])
        if entry.id is None:
            entry.sort_order = self._next_order(counters, entry.learned)
            cursor = self.connection.execute(
                "INSERT INTO words(id,text,normalized_text,important,learned,sort_order) VALUES (?,?,?,?,?,?)",
                (forced_id, entry.text, normalized, entry.important, entry.learned, entry.sort_order))
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

    def import_entries(self, drafts: list[WordEntry]) -> ImportResult:
        entries = [clean_entry(draft) for draft in drafts]
        if all(entry.sort_order is not None for entry in entries):
            entries.sort(key=lambda entry: entry.sort_order)
        result = ImportResult()
        with self._transaction() as counters:
            for entry in entries:
                entry.id = None
                existing = self.connection.execute(
                    "SELECT id FROM words WHERE normalized_text=?", (entry.text.casefold(),)).fetchone()
                if existing:
                    result.conflicts.append(ImportConflict(self.get(existing[0]), entry))
                else:
                    self._save_entry(entry, counters)
                    result.inserted += 1
        return result

    def replace_imports(self, choices: list[ImportConflict]) -> dict[str, WordEntry]:
        """Adopt in choice order, resolving names afresh after each delete/recreate."""
        entries = [clean_entry(choice.incoming) for choice in choices]
        adopted = {}
        with self._transaction() as counters:
            for entry in entries:
                normalized = entry.text.casefold()
                current = self.connection.execute(
                    "SELECT id FROM words WHERE normalized_text=?", (normalized,)).fetchone()
                if current is None:
                    raise ValueError("待替换的主词已不存在。")
                # Reserve before deleting: INTEGER PRIMARY KEY would otherwise reuse the last ID.
                new_id = self.connection.execute("SELECT COALESCE(MAX(id),0)+1 FROM words").fetchone()[0]
                self.connection.execute("DELETE FROM words WHERE id=?", (current[0],))
                entry.id = None
                self._save_entry(entry, counters, forced_id=new_id)
                adopted[normalized] = self.get(entry.id)
        return adopted

    def batch_update_sort_orders(self, updates: dict[int, int]):
        """One UPDATE for all changed ranks; missing IDs and invalid ranks fail atomically."""
        if not updates:
            return
        if any(type(key) is not int or type(value) is not int or
               not 1 <= value <= 9223372036854775807 for key, value in updates.items()):
            raise ValueError("排序号必须是 SQLite 可保存的正整数。")
        payload = json.dumps(updates)
        rows = self.connection.execute(
            "SELECT id,learned,sort_order FROM words WHERE id IN (SELECT CAST(key AS INTEGER) FROM json_each(?))",
            (payload,)).fetchall()
        if len(rows) != len(updates):
            raise ValueError("排序的单词已不存在。")
        changed = {row["id"]: updates[row["id"]] for row in rows
                   if row["sort_order"] != updates[row["id"]]}
        if not changed:
            return
        with self._transaction() as counters:
            self.connection.execute("""WITH updates AS (
                SELECT CAST(key AS INTEGER) AS id, value AS sort_order FROM json_each(?)
            ) UPDATE words SET sort_order=(SELECT sort_order FROM updates WHERE updates.id=words.id),
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id IN (SELECT id FROM updates)""", (json.dumps(changed),))
            for row in rows:
                counters[row["learned"]] = max(counters[row["learned"]], updates[row["id"]])

    def move_entry(self, entry_id: int, target_index: int):
        entry = self.get(entry_id)
        entries = [item for item in self.list_entries() if item.learned == entry.learned]
        source = next(i for i, item in enumerate(entries) if item.id == entry_id)
        if not 0 <= target_index < len(entries):
            raise ValueError("无效的目标位置。")
        if source == target_index:
            return
        entries.insert(target_index, entries.pop(source))
        rank = entries[target_index - 1].sort_order + 1 if target_index else 1
        updates = {entry_id: rank}
        for item in entries[target_index + 1:]:
            if item.sort_order > rank:
                break
            rank += 1
            updates[item.id] = rank
        self.batch_update_sort_orders(updates)

    def shuffle_pending(self):
        entries = [entry for entry in self.list_entries() if not entry.learned]
        if len(entries) < 2:
            return
        random.shuffle(entries)
        self.batch_update_sort_orders({entry.id: number for number, entry in enumerate(entries, 1)
                                       if entry.sort_order != number})

    def delete(self, entry_id: int):
        with self.connection:
            self.connection.execute("DELETE FROM words WHERE id=?", (entry_id,))

    def set_flag(self, entry_id: int, flag: str, value: bool):
        if flag not in {"important", "learned"}:
            raise ValueError("不支持的属性")
        with self._transaction() as counters:
            entry = self.get(entry_id)
            if flag == "learned" and entry.learned != bool(value):
                rank = self._next_order(counters, value)
                self.connection.execute("""UPDATE words SET learned=?,sort_order=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                    (bool(value), rank, entry_id))
            elif flag == "important":
                self.connection.execute("""UPDATE words SET important=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""", (bool(value), entry_id))

    def shortcuts(self) -> dict[str, str]:
        return {row[0]: row[1] for row in self.connection.execute("SELECT key,value FROM settings")
                if row[0] in DEFAULT_SHORTCUTS}

    def write_shortcuts(self, values: dict[str, str]):
        with self.connection:
            self.connection.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", values.items())


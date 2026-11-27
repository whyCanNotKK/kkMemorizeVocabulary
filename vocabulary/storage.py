"""SQLite aggregate repository. Every write is transactional."""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from copy import deepcopy
import json
import random
import sqlite3

from .domain import (CLEANUP_COMMANDS, DEFAULT_SHORTCUTS, DuplicateWord, RelatedWord, Sense, ValidationError,
                     WordContentValidationError, WordEntry, clean_entry, clean_word_content,
                     same_entry_content, shortcut_candidates)
from .transfer import ImportConflict, ImportResult
from .import_merge import AutoMergeResult, plan_auto_merge
from .related_links import (LinkConflict, LinkPlan, LinkSaveResult, changes as link_changes,
                            content as link_content, name as link_name, snapshot as link_snapshot,
                            validate_names)
from .learning import (STAGE_GAPS, SpellingHideSettings, StudyContext, StudyDrawSettings, StudyTask,
                       StudyTaskHistory, next_stage, study_due, study_timestamp)
from .study_transfer import RECORD_FIELDS, StudyBackup
from .migration import migrate_database
from .list_query import (PAGE_SIZE, export_entries as query_export_entries,
                         get_entries_by_ids as query_entries_by_ids, query_page)


def _content_write(method):
    """Publish local cache invalidation only after a content write succeeds."""
    @wraps(method)
    def tracked(self, *args, **kwargs):
        before = self.connection.total_changes
        value = method(self, *args, **kwargs)
        if self.connection.total_changes != before:
            self.content_revision += 1
        return value
    return tracked


class Repository:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.content_revision = 0
        # Optional owner notification for commits returned by a worker connection.
        # transfer_jobs invokes it on the owning GUI thread, never from SQLite.
        self.content_committed = None
        self.spelling_settings_revision = 0
        self.connection = sqlite3.connect(path, timeout=3)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        try:
            migrate_database(self.connection)
        except Exception:
            self.connection.close()
            raise
        maxima = dict(self.connection.execute(
            "SELECT learned,MAX(sort_order) FROM words GROUP BY learned").fetchall())
        self.max_pending_sort_order = maxima.get(0, 0)
        self.max_learned_sort_order = maxima.get(1, 0)
        with self.connection:
            configured = self.shortcuts()
            # Upgrade the previous visibility default once, when adding the edit action.
            if ('edit_selected' not in configured and configured.get('toggle_visibility') == 'Ctrl+E'
                    and 'Ctrl+S' not in configured.values()):
                self.connection.execute("UPDATE settings SET value=? WHERE key=?", ('Ctrl+S', 'toggle_visibility'))
                configured['toggle_visibility'] = 'Ctrl+S'
            used = set(configured.values())
            for action, default in DEFAULT_SHORTCUTS.items():
                if action in configured:
                    continue
                # Adding a new default must not collide with an older user's custom chord.
                value = next(candidate for candidate in shortcut_candidates(default) if candidate not in used)
                self.connection.execute("INSERT INTO settings(key,value) VALUES (?,?)", (action, value))
                used.add(value)

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
        children = [RelatedWord(child["text"], self._senses("related_id", child["id"]), child["phonetic"])
                    for child in self.connection.execute(
                        "SELECT * FROM related_words WHERE word_id=? ORDER BY position,id", (entry_id,))]
        return WordEntry(row["text"], self._senses("word_id", entry_id), children,
                         bool(row["important"]), bool(row["learned"]), entry_id, row["sort_order"], row["phonetic"])

    def list_entries(self) -> list[WordEntry]:
        return self.get_entries_by_ids(row[0] for row in self.connection.execute(
            "SELECT id FROM words ORDER BY learned,sort_order,id"))

    def get_entries_by_ids(self, ids) -> list[WordEntry]:
        return query_entries_by_ids(self.connection, ids)

    def query_page(self, category, keyword="", page_number=1, anchor_id=None, page_size=PAGE_SIZE):
        return query_page(self.connection, category, keyword, page_number, anchor_id, page_size)

    def export_entries(self, category, keyword="") -> list[WordEntry]:
        return query_export_entries(self.connection, category, keyword)

    @_content_write
    def move_related(self, entry_id: int, source: int, target: int):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            ids = [row[0] for row in self.connection.execute(
                "SELECT id FROM related_words WHERE word_id=? ORDER BY position,id", (entry_id,))]
            if (type(source) is not int or type(target) is not int
                    or not 0 <= source < len(ids) or not 0 <= target < len(ids)):
                raise ValueError("关联词排序位置已失效，请重试。")
            if source == target:
                return
            ids.insert(target, ids.pop(source))
            self.connection.executemany("UPDATE related_words SET position=? WHERE id=? AND word_id=?",
                                        [(position, child_id, entry_id) for position, child_id in enumerate(ids)])

    @_content_write
    def save(self, draft: WordEntry) -> WordEntry:
        entry = clean_entry(draft)
        with self._transaction() as counters:
            self._save_entry(entry, counters)
        return self.get(entry.id)

    def prepare_links(self, draft, baseline, *, inline=False):
        """Read only affected aggregates; imports deliberately bypass this policy."""
        draft = clean_entry(draft)
        before = self.get(draft.id) if draft.id is not None else None
        validate_names(draft, before if inline else None)
        self._check_word_name(draft.text, draft.id)
        added, removed = link_changes(baseline, draft)
        targets = {}
        for key in sorted(added | removed):
            row = self.connection.execute('SELECT id FROM words WHERE normalized_text=?', (key,)).fetchone()
            if row is not None and row[0] != draft.id:
                targets[key] = self.get(row[0])
        plan = LinkPlan(draft, before, added, removed, targets)
        for index, child in enumerate(draft.related):
            target = targets.get(link_name(child.text))
            if link_name(child.text) in added and target and link_content(child) != link_content(target):
                plan.conflicts.append(LinkConflict(target.id, index))
        return plan

    @_content_write
    def save_links(self, plan, draft=None, targets=None):
        """Atomically commit the source, comparison edits, and reverse links."""
        draft = clean_entry(draft or plan.draft)
        targets = targets or {}
        result = []
        with self._transaction() as counters:
            self.connection.execute('BEGIN IMMEDIATE')
            stale = '单词内容已变化，当前输入已保留。请取消后重新保存以检查最新内容。'
            before = self.get(draft.id) if draft.id is not None else None
            if before is not None and link_snapshot(before) != link_snapshot(plan.before):
                raise ValueError(stale)
            # Recheck both existing and absent name matches under the write lock.
            for key in plan.added | plan.removed:
                row = self.connection.execute('SELECT id FROM words WHERE normalized_text=?', (key,)).fetchone()
                actual = row[0] if row is not None and row[0] != draft.id else None
                expected = plan.targets.get(key)
                if actual != (expected.id if expected else None):
                    raise ValueError(stale)
            current_targets = {}
            for key, expected in plan.targets.items():
                current = self.get(expected.id)
                if link_snapshot(current) != link_snapshot(expected):
                    raise ValueError(stale)
                current_targets[key] = current
            self._save_entry(draft, counters)
            saved = self.get(draft.id)
            result.append((before, saved))
            for key, original in current_targets.items():
                updated = deepcopy(original)
                edited = targets.get(original.id)
                if edited is not None:
                    value = clean_word_content(RelatedWord(original.text, edited.senses, edited.phonetic))
                    updated.phonetic, updated.senses = value.phonetic, value.senses
                if key in plan.removed:
                    updated.related = [child for child in updated.related
                                       if link_name(child.text) != link_name(saved.text)]
                if key in plan.added and not any(link_name(child.text) == link_name(saved.text)
                                                 for child in updated.related):
                    updated.related.append(RelatedWord(saved.text, deepcopy(saved.senses), saved.phonetic))
                if updated != original:
                    self._save_entry(updated, counters)
                    result.append((original, self.get(updated.id)))
        return LinkSaveResult(saved, result)

    @_content_write
    def set_phonetic(self, entry_id: int, phonetic: str, related_text: str | None = None) -> WordEntry:
        """Update only a pronunciation, matching related words by name rather than position."""
        if not isinstance(phonetic, str):
            raise ValidationError("音标必须是文本。")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.get(entry_id)
            if related_text is None:
                self.connection.execute("UPDATE words SET phonetic=? WHERE id=?", (phonetic.strip(), entry_id))
            else:
                target_id = self._import_related_id(entry_id, related_text)
                if target_id is None:
                    raise ValueError(f'本地关联词“{related_text}”不存在，无法同步音标。')
                self.connection.execute("UPDATE related_words SET phonetic=? WHERE id=?",
                                        (phonetic.strip(), target_id))
            self.connection.execute(
                "UPDATE words SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (entry_id,))
            current = self.get(entry_id)
        return current

    def _import_related_id(self, entry_id, text):
        return next((row['id'] for row in self.connection.execute(
            'SELECT id,text FROM related_words WHERE word_id=? ORDER BY position,id', (entry_id,))
            if row['text'].strip().casefold() == text.strip().casefold()), None)

    @_content_write
    def sync_import_translation(self, entry_id: int, sense: Sense,
                                related_text: str | None = None) -> WordEntry:
        # Use the existing word validator, retaining even an explicitly empty translation.
        sense = clean_word_content(RelatedWord('import', [sense])).senses[0]
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            self.get(entry_id)
            column, target_id = 'word_id', entry_id
            if related_text is not None:
                column, target_id = 'related_id', self._import_related_id(entry_id, related_text)
                if target_id is None:
                    raise ValueError(f'本地关联词“{related_text}”不存在，无法同步译文。')
            existing = self.connection.execute(
                f'SELECT id FROM senses WHERE {column}=? AND pos=? ORDER BY position,id LIMIT 1',
                (target_id, sense.pos)).fetchone()
            if existing:
                self.connection.execute('UPDATE senses SET translation=? WHERE id=?',
                                        (sense.translation, existing['id']))
            else:
                self.connection.execute(f'''INSERT INTO senses({column},pos,translation,position)
                    SELECT ?,?,?,COALESCE(MAX(position),-1)+1 FROM senses WHERE {column}=?''',
                    (target_id, sense.pos, sense.translation, target_id))
            self.connection.execute(
                "UPDATE words SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (entry_id,))
            current = self.get(entry_id)
        return current

    @_content_write
    def append_import_related(self, entry_id: int, related: RelatedWord) -> WordEntry:
        related = clean_word_content(related)
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            self.get(entry_id)
            if self._import_related_id(entry_id, related.text) is None:
                cursor = self.connection.execute('''INSERT INTO related_words(word_id,text,position,phonetic)
                    SELECT ?,?,COALESCE(MAX(position),-1)+1,? FROM related_words WHERE word_id=?''',
                    (entry_id, related.text, related.phonetic, entry_id))
                self.connection.executemany(
                    'INSERT INTO senses(related_id,pos,translation,position) VALUES (?,?,?,?)',
                    [(cursor.lastrowid, sense.pos, sense.translation, index)
                     for index, sense in enumerate(related.senses)])
                self.connection.execute(
                    "UPDATE words SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (entry_id,))
            current = self.get(entry_id)
        return current

    @_content_write
    def replace_word_content(self, expected: WordEntry, owner: int, word: RelatedWord):
        """Replace just the target word and its definitions from a list snapshot."""
        word = clean_word_content(word)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            entry = self.get(expected.id)
            stale = "单词内容已变化，当前输入已保留。"
            if owner == -1:
                if (entry.text, entry.phonetic, entry.senses) != (expected.text, expected.phonetic, expected.senses):
                    raise ValueError(stale)
                normalized = self._check_word_name(word.text, entry.id)
                self.connection.execute(
                    "UPDATE words SET text=?,normalized_text=?,phonetic=? WHERE id=?",
                    (word.text, normalized, word.phonetic, entry.id))
                column, target_id = "word_id", entry.id
            else:
                if entry.related != expected.related:
                    raise ValueError(stale)
                column, target_id = self._sense_owner(entry.id, owner)
                self.connection.execute("UPDATE related_words SET text=?,phonetic=? WHERE id=?",
                                        (word.text, word.phonetic, target_id))
            self.connection.execute(
                "UPDATE words SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (entry.id,))
            self.connection.execute(f"DELETE FROM senses WHERE {column}=?", (target_id,))
            self.connection.executemany(
                f"INSERT INTO senses({column},pos,translation,position) VALUES (?,?,?,?)",
                [(target_id, sense.pos, sense.translation, i) for i, sense in enumerate(word.senses)])

    def _sense_owner(self, entry_id, owner):
        if type(owner) is int and owner == -1:
            return "word_id", entry_id
        if type(owner) is int and owner >= 0:
            child = self.connection.execute(
                "SELECT id FROM related_words WHERE word_id=? ORDER BY position,id LIMIT 1 OFFSET ?",
                (entry_id, owner)).fetchone()
            if child is not None:
                return "related_id", child[0]
        raise ValueError("释义所属单词已失效，请重试。")

    @_content_write
    def remove_inline_content(self, expected: WordEntry, owner: int, sense: int | None,
                              context: StudyContext | None):
        """Remove a blank list field while the displayed target is still current."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            entry = self.get(expected.id)
            stale = "单词内容已变化，当前输入已保留。"
            if owner == -1:
                if sense is None and entry.related:
                    raise WordContentValidationError("单词拼写", "单词拼写不可为空")
                if (entry.text, entry.phonetic, entry.senses) != (expected.text, expected.phonetic, expected.senses):
                    raise ValueError(stale)
                word = entry
            else:
                if entry.related != expected.related:
                    raise ValueError(stale)
                word = entry.related[owner]

            if sense is None and owner == -1:
                self._delete_word(entry.id, context or StudyContext.now())
                return
            column, target_id = self._sense_owner(entry.id, owner)
            if sense is None:
                self.connection.execute("DELETE FROM related_words WHERE id=?", (target_id,))
            else:
                if len(word.senses) < 2 or not 0 <= sense < len(word.senses):
                    raise ValueError(stale)
                self.connection.execute(f"""DELETE FROM senses WHERE id=(
                    SELECT id FROM senses WHERE {column}=? ORDER BY position,id LIMIT 1 OFFSET ?
                )""", (target_id, sense))
            self.connection.execute(
                "UPDATE words SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (entry.id,))

    @_content_write
    def move_sense(self, entry_id: int, owner: int, source: int, target: int):
        """Reorder definitions of one word without rewriting content or study state."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            column, owner_id = self._sense_owner(entry_id, owner)
            ids = [row[0] for row in self.connection.execute(
                f"SELECT id FROM senses WHERE {column}=? ORDER BY position,id", (owner_id,))]
            if (type(source) is not int or type(target) is not int
                    or not 0 <= source < len(ids) or not 0 <= target < len(ids)):
                raise ValueError("释义排序位置已失效，请重试。")
            if source == target:
                return
            ids.insert(target, ids.pop(source))
            self.connection.executemany(f"UPDATE senses SET position=? WHERE id=? AND {column}=?",
                                        [(position, sense_id, owner_id) for position, sense_id in enumerate(ids)])

    def _check_word_name(self, text, entry_id):
        normalized = text.casefold()
        existing = self.connection.execute("SELECT id FROM words WHERE normalized_text=?", (normalized,)).fetchone()
        if existing and existing[0] != entry_id:
            raise DuplicateWord(existing[0])
        return normalized

    def _save_entry(self, entry: WordEntry, counters):
        """Write within the caller's transaction; ordinary edits keep learned state."""
        normalized = self._check_word_name(entry.text, entry.id)
        if entry.id is None:
            entry.sort_order = self._next_order(counters, entry.learned)
            cursor = self.connection.execute(
                "INSERT INTO words(text,normalized_text,important,learned,sort_order,phonetic) VALUES (?,?,?,?,?,?)",
                (entry.text, normalized, entry.important, entry.learned, entry.sort_order, entry.phonetic))
            entry.id = cursor.lastrowid
        else:
            cursor = self.connection.execute("""UPDATE words SET text=?,normalized_text=?,important=?,phonetic=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                (entry.text, normalized, entry.important, entry.phonetic, entry.id))
            if cursor.rowcount != 1:
                raise ValueError("该单词已不存在，当前输入已保留。")
            self.connection.execute("DELETE FROM senses WHERE word_id=?", (entry.id,))
            self.connection.execute("DELETE FROM related_words WHERE word_id=?", (entry.id,))
        self.connection.executemany(
            "INSERT INTO senses(word_id,pos,translation,position) VALUES (?,?,?,?)",
            [(entry.id, s.pos, s.translation, i) for i, s in enumerate(entry.senses)])
        for i, child in enumerate(entry.related):
            cursor = self.connection.execute(
                "INSERT INTO related_words(word_id,text,position,phonetic) VALUES (?,?,?,?)",
                (entry.id, child.text, i, child.phonetic))
            self.connection.executemany(
                "INSERT INTO senses(related_id,pos,translation,position) VALUES (?,?,?,?)",
                [(cursor.lastrowid, s.pos, s.translation, j) for j, s in enumerate(child.senses)])

    @_content_write
    def import_entries(self, drafts: list[WordEntry]) -> ImportResult:
        entries = [(index, clean_entry(draft)) for index, draft in enumerate(drafts)]
        for _, entry in entries:
            for word in [entry, *entry.related]:
                while word.phonetic.startswith('/') or word.phonetic.endswith('/'):
                    word.phonetic = word.phonetic.strip('/').strip()
        if all(entry.sort_order is not None for _, entry in entries):
            entries.sort(key=lambda item: item[1].sort_order)
        result = ImportResult()
        with self._transaction() as counters:
            for source_index, entry in entries:
                entry.id = None
                existing = self.connection.execute(
                    "SELECT id FROM words WHERE normalized_text=?", (entry.text.casefold(),)).fetchone()
                if existing:
                    current = self.get(existing[0])
                    if not same_entry_content(current, entry):
                        result.conflicts.append(ImportConflict(current, entry, source_index))
                else:
                    self._save_entry(entry, counters)
                    result.inserted += 1
        return result

    @_content_write
    def replace_imports(self, choices: list[ImportConflict]) -> dict[str, WordEntry]:
        """Adopt in place, moving to a category's end only when learned state changes."""
        entries = [clean_entry(choice.incoming) for choice in choices]
        adopted = {}
        with self._transaction() as counters:
            self.connection.execute('BEGIN IMMEDIATE')
            for entry in entries:
                normalized = entry.text.casefold()
                current = self.connection.execute(
                    "SELECT id,learned FROM words WHERE normalized_text=?", (normalized,)).fetchone()
                if current is None:
                    raise ValueError("待替换的主词已不存在。")
                entry.id = current['id']
                self._save_entry(entry, counters)
                if bool(current['learned']) != entry.learned:
                    rank = self._next_order(counters, entry.learned)
                    self.connection.execute('UPDATE words SET learned=?,sort_order=? WHERE id=?',
                                            (entry.learned, rank, entry.id))
                adopted[normalized] = self.get(entry.id)
        return adopted

    @_content_write
    def auto_merge_imports(self, choices: list[ImportConflict]) -> AutoMergeResult:
        """Process candidates once in order, committing all safe changes atomically."""
        result = AutoMergeResult()
        with self._transaction() as counters:
            self.connection.execute('BEGIN IMMEDIATE')
            for index, choice in enumerate(choices):
                incoming = clean_entry(choice.incoming)
                name = incoming.text.casefold()
                row = self.connection.execute(
                    'SELECT id FROM words WHERE normalized_text=?', (name,)).fetchone()
                if row is None:
                    raise ValueError('待合并的主词已不存在。')
                current = self.get(row['id'])
                decision = plan_auto_merge(current, incoming)
                if decision.entry is None:
                    result.skipped[index] = decision.reason
                else:
                    if same_entry_content(current, decision.entry):
                        result.stats.unchanged += 1
                    else:
                        self._save_entry(decision.entry, counters)
                        current = self.get(current.id)
                        result.stats.changed += 1
                    result.processed_indices.append(index)
                result.entries[name] = current
        return result

    @_content_write
    def batch_update_sort_orders(self, updates: dict[int, int]):
        """One UPDATE for all changed ranks; missing IDs and invalid ranks fail atomically."""
        if not updates:
            return {}
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
            return {}
        with self._transaction() as counters:
            self.connection.execute("""WITH updates AS (
                SELECT CAST(key AS INTEGER) AS id, value AS sort_order FROM json_each(?)
            ) UPDATE words SET sort_order=(SELECT sort_order FROM updates WHERE updates.id=words.id),
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id IN (SELECT id FROM updates)""", (json.dumps(changed),))
            for row in rows:
                counters[row["learned"]] = max(counters[row["learned"]], updates[row["id"]])
        return changed

    @_content_write
    def move_entry(self, entry_id: int, target_index: int):
        # Read only ordering metadata; never hydrate the words being shifted.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            entry = self.connection.execute(
                "SELECT learned,sort_order,id FROM words WHERE id=?", (entry_id,)).fetchone()
            if entry is None:
                raise ValueError("该单词已不存在。")
            learned = entry["learned"]
            total = self.connection.execute(
                "SELECT COUNT(*) FROM words WHERE learned=?", (learned,)).fetchone()[0]
            if type(target_index) is not int or not 0 <= target_index < total:
                raise ValueError("无效的目标位置。")
            source = self.connection.execute("""SELECT COUNT(*) FROM words
                WHERE learned=? AND (sort_order,id)<(?,?)""",
                (learned, entry["sort_order"], entry_id)).fetchone()[0]
            if source == target_index:
                return {}
            previous = self.connection.execute("""SELECT sort_order,id FROM words
                WHERE learned=? AND id<>? ORDER BY sort_order,id LIMIT 1 OFFSET ?""",
                (learned, entry_id, target_index - 1)).fetchone() if target_index else None
            rank = previous["sort_order"] + 1 if previous is not None else 1
            updates = {entry_id: rank}
            suffix = self.connection.execute("""SELECT id,sort_order FROM words
                WHERE learned=? AND id<>? ORDER BY sort_order,id LIMIT -1 OFFSET ?""",
                (learned, entry_id, target_index))
            for item in suffix:
                if item["sort_order"] > rank:
                    break
                rank += 1
                updates[item["id"]] = rank
            return self.batch_update_sort_orders(updates)

    @_content_write
    def shuffle_entries(self, page: str):
        if page not in {"pending", "learned", "all"}:
            raise ValueError("无效的随机排列分类。")
        groups = {False: [], True: []}
        for row in self.connection.execute("SELECT id,learned,sort_order FROM words ORDER BY learned,sort_order,id"):
            groups[bool(row["learned"])].append(row)
        states = (False, True) if page == "all" else (page == "learned",)
        updates = {}
        for learned in states:
            entries = groups[learned]
            if len(entries) < 2:
                continue
            random.shuffle(entries)
            updates.update({entry["id"]: number for number, entry in enumerate(entries, 1)
                            if entry["sort_order"] != number})
        return self.batch_update_sort_orders(updates)

    @_content_write
    def delete(self, entry_id: int, context: StudyContext | None = None):
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            self._delete_word(entry_id, context or StudyContext.now())

    def _delete_word(self, entry_id, context):
        progress = self.connection.execute(
            'SELECT * FROM memorize_vocabulary_17day WHERE word_id=?', (entry_id,)).fetchone()
        history = self.study_task_history(context)
        if progress is not None and history is not None:
            completed_today = progress['last_completed_on'] == context.day.isoformat()
            if completed_today or study_due(progress['stage'], progress['last_completed_on'], context.day):
                kind = ('new' if progress['stage'] == 1 or
                        (completed_today and progress['stage'] == 2) else 'review')
                done = completed_today or progress['afternoon_done' if context.afternoon else 'morning_done']
                total = max(0, getattr(history, f'{kind}_total') - 1)
                count = min(total, max(0, getattr(history, f'{kind}_done') - int(bool(done))))
                self.connection.execute(f'''UPDATE study_task_history
                    SET {kind}_total=?,{kind}_done=? WHERE id=?''', (total, count, history.id))
        self.connection.execute("DELETE FROM words WHERE id=?", (entry_id,))

    def clear_content(self, command: str):
        """Clear the requested scope atomically, including dependent word content."""
        if command not in CLEANUP_COMMANDS:
            raise ValueError("无效的清空命令。")
        removed_words = 0
        with self._transaction() as counters:
            self.connection.execute('BEGIN IMMEDIATE')
            if command == 'clean vocabulary only':
                removed_words = self.connection.execute('''DELETE FROM words WHERE NOT EXISTS
                    (SELECT 1 FROM memorize_vocabulary_17day WHERE word_id=words.id)''').rowcount
            elif command == 'remove all':
                removed_words = self.connection.execute('DELETE FROM words').rowcount
                counters[:] = [0, 0]
            if command != 'clean vocabulary only':
                self.connection.execute('DELETE FROM memorize_vocabulary_17day')
                self.connection.execute('DELETE FROM study_task_history')
                self.connection.execute('''DELETE FROM settings WHERE key IN
                    ('study_last_add_new_vocabulary', 'study17.group_counter')''')
        if removed_words:
            self.content_revision += 1

    @_content_write
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

    def study_draw_settings(self) -> StudyDrawSettings:
        values = dict(self.connection.execute("""SELECT key,value FROM settings
            WHERE key IN ('study17.words_per_group','study17.groups_per_day')"""))
        defaults = StudyDrawSettings()
        return StudyDrawSettings(
            int(values.get('study17.words_per_group', defaults.words_per_group)),
            int(values.get('study17.groups_per_day', defaults.groups_per_day)))

    def write_study_draw_settings(self, settings: StudyDrawSettings):
        with self.connection:
            self.connection.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", [
                ('study17.words_per_group', str(settings.words_per_group)),
                ('study17.groups_per_day', str(settings.groups_per_day)),
            ])

    def prepare_study(self, context: StudyContext, entries_by_id=None) -> list[StudyTask]:
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            self._draw_study(context)
        return self.study_tasks(context, entries_by_id)

    _NEW_STUDY_WORDS = '''FROM words WHERE learned=0 AND NOT EXISTS
        (SELECT 1 FROM memorize_vocabulary_17day WHERE word_id=words.id)'''

    def has_new_study_words(self) -> bool:
        return self.connection.execute('SELECT 1 ' + self._NEW_STUDY_WORDS + ' LIMIT 1').fetchone() is not None

    def study_new_words_enabled(self) -> bool:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key='study17.new_words_enabled'").fetchone()
        return row is None or row[0] == '1'

    def study_hide_word_while_spelling(self) -> bool:
        return self.study_spelling_hide_settings().mode == 'all'

    def set_study_hide_word_while_spelling(self, enabled: bool):
        self.set_study_spelling_hide_settings(SpellingHideSettings.from_enabled(enabled))

    def study_spelling_hide_settings(self) -> SpellingHideSettings:
        prefix = 'study17.hide_word_while_spelling'
        values = dict(self.connection.execute(
            'SELECT key,value FROM settings WHERE key=? OR key LIKE ?', (prefix, prefix + '.%')))
        mode = {'0': 'off', '1': 'all'}.get(values.get(prefix), values.get(prefix, 'off'))
        default_flag = '1' if mode == 'all' else '0'
        return SpellingHideSettings(mode,
            *(values.get(prefix + '.' + key, default_flag) == '1'
              for key in ('by_stage', 'by_important', 'by_learned')),
            int(values.get(prefix + '.minimum_stage', '15')))

    def _write_study_spelling_hide_settings(self, settings: SpellingHideSettings):
        prefix = 'study17.hide_word_while_spelling'
        self.connection.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', [
            (prefix, {'off': '0', 'all': '1', 'conditional': 'conditional'}[settings.mode]),
            (prefix + '.by_stage', str(int(settings.by_stage))),
            (prefix + '.by_important', str(int(settings.by_important))),
            (prefix + '.by_learned', str(int(settings.by_learned))),
            (prefix + '.minimum_stage', str(settings.minimum_stage)),
        ])

    def set_study_spelling_hide_settings(self, settings: SpellingHideSettings):
        previous = self.study_spelling_hide_settings()
        with self.connection:
            self._write_study_spelling_hide_settings(settings)
        if settings != previous:
            self.spelling_settings_revision += 1

    def initialize_study_new_words(self):
        """Run once at application startup, never when opening worker connections."""
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            enabled = self.study_new_words_enabled() and self.has_new_study_words()
            self._write_study_new_words_enabled(enabled)

    def _write_study_new_words_enabled(self, enabled):
        self.connection.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)',
                                ('study17.new_words_enabled', '1' if enabled else '0'))

    def set_study_new_words_enabled(self, enabled: bool, context: StudyContext):
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            previous = self.study_new_words_enabled()
            if enabled and not previous and not self.has_new_study_words():
                raise ValidationError('当前待记单词列表中的词均已背过')
            self._write_study_new_words_enabled(enabled)
            if enabled and not previous:
                self._draw_study(context)

    def _draw_study(self, context):
        """Caller owns the transaction; only a nonempty draw consumes the date."""
        if context.afternoon or not self.study_new_words_enabled():
            return
        key = 'study_last_add_new_vocabulary'
        generated = self.connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if generated is not None and generated[0] == context.day.isoformat():
            return
        settings = self.study_draw_settings()
        candidates = self.connection.execute('SELECT id,important ' + self._NEW_STUDY_WORDS).fetchall()
        important_ids = [row['id'] for row in candidates if row['important']]
        ids = random.sample(important_ids, min(settings.daily_word_count * 4 // 5, len(important_ids)))
        selected = set(ids)
        remaining = [row['id'] for row in candidates if row['id'] not in selected]
        ids.extend(random.sample(remaining, min(settings.daily_word_count - len(ids), len(remaining))))
        if not ids:
            return
        random.shuffle(ids)
        counter = self.connection.execute("SELECT value FROM settings WHERE key='study17.group_counter'").fetchone()
        maximum = int(counter[0]) if counter else 0
        self.connection.executemany('''INSERT INTO memorize_vocabulary_17day
            (word_id,group_number) VALUES (?,?)''',
            [(word_id, maximum + i // settings.words_per_group + 1) for i, word_id in enumerate(ids)])
        self.connection.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)',
                                ('study17.group_counter', str(maximum + (
                                    len(ids) + settings.words_per_group - 1) // settings.words_per_group)))
        self.connection.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', (key, context.day.isoformat()))
        # A user may enable new words after starting or finishing today's review.
        self.connection.execute('''UPDATE study_task_history
            SET new_total=new_total+?,status=0,ended_at=NULL WHERE task_date=?''',
            (len(ids), context.day.isoformat()))

    @staticmethod
    def _study_due_clause():
        wait = 'CASE stage ' + ' '.join(f'WHEN {stage} THEN {gap}' for stage, gap in STAGE_GAPS.items()) + ' END'
        return f"stage<>0 AND (stage=1 OR julianday(?)-julianday(last_completed_on)>={wait})"

    def study_tasks(self, context: StudyContext, entries_by_id=None) -> list[StudyTask]:
        flag = "afternoon_done" if context.afternoon else "morning_done"
        rows = self.connection.execute(f"""SELECT word_id,group_number,stage FROM memorize_vocabulary_17day
            WHERE {flag}=0 AND ({self._study_due_clause()})
            ORDER BY group_number,word_id""", (context.day.isoformat(),)).fetchall()
        entries = self._study_entries(rows, entries_by_id)
        return [StudyTask(entries[row['word_id']], row['group_number'], row['stage'], row['stage'] != 1)
                for row in rows]

    def _study_entries(self, rows, entries_by_id):
        entries = entries_by_id if entries_by_id is not None else {}
        missing = [row['word_id'] for row in rows if row['word_id'] not in entries]
        if missing:
            entries.update({entry.id: entry for entry in self.get_entries_by_ids(missing)})
        return entries

    def study_completed_tasks(self, context: StudyContext, entries_by_id=None) -> list[StudyTask]:
        flag = 'afternoon_done' if context.afternoon else 'morning_done'
        day = context.day.isoformat()
        rows = self.connection.execute(f'''SELECT word_id,group_number,stage,last_completed_on
            FROM memorize_vocabulary_17day
            WHERE ({flag}=1 AND ({self._study_due_clause()})) OR last_completed_on=?
            ORDER BY group_number,word_id''', (day, day)).fetchall()
        entries = self._study_entries(rows, entries_by_id)
        return [StudyTask(entries[row['word_id']], row['group_number'], row['stage'],
                          not (row['stage'] == 1 or (row['stage'] == 2 and row['last_completed_on'] == day)))
                for row in rows]

    def study_task_total(self, context: StudyContext) -> int:
        # Completing both periods advances stage and clears flags; retain those words in today's total.
        day = context.day.isoformat()
        return self.connection.execute(f"""SELECT COUNT(*) FROM memorize_vocabulary_17day
            WHERE ({self._study_due_clause()}) OR last_completed_on=?""", (day, day)).fetchone()[0]

    def study_participant_count(self) -> int:
        return self.connection.execute('SELECT COUNT(*) FROM memorize_vocabulary_17day').fetchone()[0]

    def study_completed_count(self) -> int:
        return self.connection.execute(
            'SELECT COUNT(*) FROM memorize_vocabulary_17day WHERE stage=0').fetchone()[0]

    def has_study_progress(self) -> bool:
        return (self.connection.execute('SELECT 1 FROM memorize_vocabulary_17day LIMIT 1').fetchone() is not None
                or self.connection.execute('SELECT 1 FROM study_task_history LIMIT 1').fetchone() is not None)

    def study_task_history(self, context: StudyContext) -> StudyTaskHistory | None:
        row = self.connection.execute('SELECT * FROM study_task_history WHERE task_date=? AND afternoon=?',
                                      (context.day.isoformat(), int(context.afternoon))).fetchone()
        return StudyTaskHistory(**dict(row)) if row is not None else None

    def study_task_histories(self) -> list[StudyTaskHistory]:
        return [StudyTaskHistory(**dict(row)) for row in self.connection.execute(
            'SELECT * FROM study_task_history ORDER BY task_date DESC,afternoon DESC')]

    def latest_study_end(self) -> datetime | None:
        ended_at = self.connection.execute(
            'SELECT MAX(ended_at) FROM study_task_history WHERE status=1').fetchone()[0]
        return datetime.fromisoformat(ended_at) if ended_at is not None else None

    def _start_study_task(self, context, instant):
        history = self.study_task_history(context)
        if history is not None:
            return history
        flag = 'afternoon_done' if context.afternoon else 'morning_done'
        rows = self.connection.execute(f'''SELECT stage FROM memorize_vocabulary_17day
            WHERE {flag}=0 AND ({self._study_due_clause()})''', (context.day.isoformat(),)).fetchall()
        if not rows:
            return None
        new = sum(row['stage'] == 1 for row in rows)
        self.connection.execute('''INSERT INTO study_task_history
            (task_date,afternoon,started_at,new_total,review_total) VALUES (?,?,?,?,?)''',
            (context.day.isoformat(), int(context.afternoon), study_timestamp(instant), new, len(rows) - new))
        return self.study_task_history(context)

    def start_study_task(self, context: StudyContext, *, instant=None) -> StudyTaskHistory | None:
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            return self._start_study_task(context, instant)

    def save_study_duration(self, context: StudyContext, duration_seconds: int):
        if type(duration_seconds) is not int or duration_seconds < 0:
            raise ValueError('任务时长必须是非负整数秒。')
        with self.connection:
            self.connection.execute('''UPDATE study_task_history SET duration_seconds=MAX(duration_seconds,?)
                WHERE task_date=? AND afternoon=? AND status=0''',
                (duration_seconds, context.day.isoformat(), int(context.afternoon)))

    def study_backup(self) -> StudyBackup:
        """Read entries, progress and generation markers from the same WAL snapshot."""
        with self.connection:
            self.connection.execute('BEGIN')
            rows = self.connection.execute('''SELECT * FROM memorize_vocabulary_17day
                ORDER BY group_number,word_id''').fetchall()
            words, records = [], {}
            for row in rows:
                word = self.get(row['word_id'])
                words.append(word)
                record = {field: row[field] for field in RECORD_FIELDS}
                for flag in ('morning_done', 'afternoon_done'):
                    record[flag] = bool(record[flag])
                records[word.text.strip().casefold()] = record
            last_draw = self.connection.execute(
                "SELECT value FROM settings WHERE key='study_last_add_new_vocabulary'").fetchone()
            counter = self.connection.execute(
                "SELECT value FROM settings WHERE key='study17.group_counter'").fetchone()
            history = [dict(row) for row in self.connection.execute('SELECT * FROM study_task_history ORDER BY id')]
            draw = self.study_draw_settings()
            return StudyBackup(words, records, last_draw[0] if last_draw else None,
                               int(counter[0]) if counter else 0, history,
                               new_words_enabled=self.study_new_words_enabled(),
                               words_per_group=draw.words_per_group, groups_per_day=draw.groups_per_day,
                               spelling_hide=self.study_spelling_hide_settings())

    def replace_study_progress(self, backup: StudyBackup):
        """Resolve final local word IDs, then atomically replace progress and its settings."""
        previous_spelling_settings = self.study_spelling_hide_settings()
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            values = []
            for name, record in backup.records.items():
                word = self.connection.execute('SELECT id FROM words WHERE normalized_text=?', (name,)).fetchone()
                if word is None:
                    raise ValueError(f'单词 {name} 尚未导入，原学习进度已保留。')
                values.append((word[0], *(record[field] for field in RECORD_FIELDS)))
            self.connection.execute('DELETE FROM memorize_vocabulary_17day')
            self.connection.executemany('''INSERT INTO memorize_vocabulary_17day
                (word_id,group_number,stage,morning_done,afternoon_done,last_completed_on)
                VALUES (?,?,?,?,?,?)''', values)
            self.connection.execute('DELETE FROM study_task_history')
            for record in backup.task_history:
                self.connection.execute('''INSERT INTO study_task_history
                    (id,task_date,afternoon,started_at,ended_at,duration_seconds,status,
                     new_total,review_total,new_done,review_done) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    tuple(record[field] for field in StudyTaskHistory.__dataclass_fields__))
            self.connection.execute("DELETE FROM settings WHERE key IN ('study_last_add_new_vocabulary','study17.group_counter')")
            self.connection.executemany('INSERT INTO settings(key,value) VALUES (?,?)',
                ([('study_last_add_new_vocabulary', backup.study_last_add_new_vocabulary)]
                 if backup.study_last_add_new_vocabulary is not None else [])
                + [('study17.group_counter', str(backup.group_counter))])
            self.connection.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', [
                ('study17.new_words_enabled', '1' if backup.new_words_enabled else '0'),
                ('study17.words_per_group', str(backup.words_per_group)),
                ('study17.groups_per_day', str(backup.groups_per_day)),
            ])
            self._write_study_spelling_hide_settings(backup.spelling_hide)
        if backup.spelling_hide != previous_spelling_settings:
            self.spelling_settings_revision += 1

    def complete_study(self, word_id: int, context: StudyContext, *, duration_seconds=0,
                       instant=None) -> tuple[StudyTaskHistory, int] | bool:
        """Return the updated history and stage, or False for a duplicate completion."""
        if type(duration_seconds) is not int or duration_seconds < 0:
            raise ValueError('任务时长必须是非负整数秒。')
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT * FROM memorize_vocabulary_17day WHERE word_id=?", (word_id,)).fetchone()
            if row is None:
                raise ValueError("该单词的学习记录已不存在，请刷新。")
            flag = "afternoon_done" if context.afternoon else "morning_done"
            if not study_due(row['stage'], row['last_completed_on'], context.day) or row[flag]:
                return False
            history = self._start_study_task(context, instant)
            if history.status:
                raise ValueError('当前时段的任务已经完成，请刷新学习任务。')
            stage = row["stage"]
            if not context.afternoon:
                self.connection.execute("""UPDATE memorize_vocabulary_17day
                    SET morning_done=1,afternoon_done=0 WHERE word_id=?""", (word_id,))
            elif not row["morning_done"]:
                self.connection.execute("""UPDATE memorize_vocabulary_17day
                    SET afternoon_done=1 WHERE word_id=?""", (word_id,))
            else:
                stage = next_stage(stage)
                self.connection.execute("""UPDATE memorize_vocabulary_17day
                    SET morning_done=0,afternoon_done=0,last_completed_on=?,
                        stage=? WHERE word_id=?""", (context.day.isoformat(), stage, word_id))
            kind = 'new' if row['stage'] == 1 else 'review'
            finished = self.connection.execute(f'''SELECT 1 FROM memorize_vocabulary_17day
                WHERE {flag}=0 AND ({self._study_due_clause()}) LIMIT 1''',
                (context.day.isoformat(),)).fetchone() is None
            if finished:
                self.connection.execute(f'''UPDATE study_task_history
                    SET {kind}_done=MIN({kind}_total,{kind}_done+1),
                        duration_seconds=MAX(duration_seconds,?),status=1,ended_at=? WHERE id=?''',
                    (duration_seconds, study_timestamp(instant), history.id))
            else:
                self.connection.execute(f'''UPDATE study_task_history
                    SET {kind}_done=MIN({kind}_total,{kind}_done+1) WHERE id=?''', (history.id,))
            return self.study_task_history(context), stage

    def write_shortcuts(self, values: dict[str, str]):
        with self.connection:
            self.connection.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", values.items())


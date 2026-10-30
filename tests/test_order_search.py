from dataclasses import replace
import json
import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.storage import Repository
from vocabulary.transfer import parse_entries, read_entries, serialize_entries
from vocabulary.widgets import PosCombo


def word(text, *, learned=False, order=None):
    return WordEntry(text, [Sense("n.", "译文 " + text)], learned=learned, sort_order=order)


def visible(window):
    return [row.entry.text for row in window.model.rows if row.first]


def test_v1_migration_preserves_each_category_order_and_aggregates(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE words (id INTEGER PRIMARY KEY, text TEXT NOT NULL,
            normalized_text TEXT NOT NULL UNIQUE, important INTEGER NOT NULL DEFAULT 1,
            learned INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT 'old', updated_at TEXT DEFAULT 'old');
        CREATE TABLE related_words (id INTEGER PRIMARY KEY, word_id INTEGER REFERENCES words(id) ON DELETE CASCADE,
            text TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE senses (id INTEGER PRIMARY KEY, word_id INTEGER REFERENCES words(id) ON DELETE CASCADE,
            related_id INTEGER REFERENCES related_words(id) ON DELETE CASCADE,
            pos TEXT NOT NULL, translation TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO words(id,text,normalized_text,learned) VALUES
            (3,'p1','p1',0),(7,'l1','l1',1),(10,'p2','p2',0),(22,'l2','l2',1);
        PRAGMA user_version=1;
    """)
    connection.close()
    repo = Repository(path)
    try:
        assert repo.connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert [(e.id, e.sort_order) for e in repo.list_entries()] == [(3, 1), (10, 2), (7, 1), (22, 2)]
        assert repo.max_pending_sort_order == repo.max_learned_sort_order == 2
        assert repo.save(word("p3")).sort_order == 3
    finally:
        repo.close()
    repo = Repository(path)
    try:
        assert repo.max_pending_sort_order == 3 and repo.max_learned_sort_order == 2
        assert repo.get(10).sort_order == 2
    finally:
        repo.close()


def test_counter_allocation_edit_transition_delete_and_restart(repo):
    first = repo.save(word("one"))
    second = repo.save(word("two", learned=True))
    assert first.sort_order == second.sort_order == 1
    repo.batch_update_sort_orders({first.id: 50})
    repo.save(replace(first, text="ONE", sort_order=900, learned=True))
    assert repo.get(first.id).sort_order == 50 and not repo.get(first.id).learned
    repo.set_flag(first.id, "learned", True)
    assert repo.get(first.id).sort_order == 2
    repo.set_flag(first.id, "learned", True)
    assert repo.max_learned_sort_order == 2
    repo.set_flag(first.id, "learned", False)
    assert repo.get(first.id).sort_order == 51
    repo.delete(first.id)
    assert repo.save(word("three")).sort_order == 52
    reopened = Repository(repo.path)
    try:
        assert reopened.max_pending_sort_order == 52 and reopened.max_learned_sort_order == 1
    finally:
        reopened.close()


def test_sort_by_state_then_number_then_id(repo):
    a = repo.save(word("a", learned=True))
    b = repo.save(word("b"))
    c = repo.save(word("c"))
    repo.batch_update_sort_orders({a.id: 1, b.id: 8, c.id: 8})
    assert [e.id for e in repo.list_entries()] == [b.id, c.id, a.id]


def test_move_minimal_cascade_and_single_update(repo):
    entries = [repo.save(word(text)) for text in "abcdefgx"]
    repo.batch_update_sort_orders(dict(zip([e.id for e in entries], [1, 1, 2, 3, 6, 6, 9, 20])))
    repo.connection.executescript("""CREATE TABLE audit (id INTEGER);
        CREATE TRIGGER rank_audit AFTER UPDATE OF sort_order ON words
        BEGIN INSERT INTO audit VALUES (NEW.id); END;""")
    statements = []
    repo.connection.set_trace_callback(statements.append)
    repo.move_entry(entries[-1].id, 1)
    repo.connection.set_trace_callback(None)
    assert [(e.text, e.sort_order) for e in repo.list_entries()] == [
        ("a", 1), ("x", 2), ("b", 3), ("c", 4), ("d", 5), ("e", 6), ("f", 6), ("g", 9)]
    assert {row[0] for row in repo.connection.execute("SELECT id FROM audit")} == {
        entries[i].id for i in (7, 1, 2, 3)}
    # Triggers cause trace_callback to repeat the same outer statement; no per-row UPDATE is issued.
    updates = {sql for sql in statements if "UPDATE words SET" in sql}
    assert len(updates) == 1 and "json_each" in next(iter(updates))
    assert repo.max_pending_sort_order == 20


@pytest.mark.parametrize("learned", [False, True])
def test_move_first_last_and_noop(repo, learned):
    a, b, c = [repo.save(word(text, learned=learned)) for text in "abc"]
    repo.move_entry(c.id, 0)
    assert [(e.text, e.sort_order) for e in repo.list_entries()] == [("c", 1), ("a", 2), ("b", 3)]
    repo.move_entry(c.id, 2)
    assert [(e.text, e.sort_order) for e in repo.list_entries()] == [("a", 2), ("b", 3), ("c", 4)]
    statements = []
    repo.connection.set_trace_callback(statements.append)
    repo.move_entry(c.id, 2)
    repo.connection.set_trace_callback(None)
    assert not any("UPDATE words" in sql for sql in statements)


def test_batch_rank_rollback_does_not_publish_maxima(repo):
    a, b = repo.save(word("a")), repo.save(word("b"))
    repo.connection.executescript(f"""CREATE TRIGGER fail_rank BEFORE UPDATE OF sort_order ON words
        WHEN NEW.id={b.id} BEGIN SELECT RAISE(ABORT,'FAIL'); END;""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.batch_update_sort_orders({a.id: 100, b.id: 101})
    assert repo.list_entries() == [a, b]
    assert repo.max_pending_sort_order == 2
    assert repo.save(word("c")).sort_order == 3


def test_failed_import_and_transition_keep_counters(repo):
    a = repo.save(word("a"))
    repo.connection.executescript("""CREATE TRIGGER fail_sense BEFORE INSERT ON senses
        WHEN NEW.translation='FAIL' BEGIN SELECT RAISE(ABORT,'FAIL'); END;
        CREATE TRIGGER fail_state BEFORE UPDATE OF learned ON words
        BEGIN SELECT RAISE(ABORT,'FAIL'); END;""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.import_entries([word("b"), WordEntry("c", [Sense("n.", "FAIL")], learned=True)])
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_flag(a.id, "learned", True)
    assert repo.list_entries() == [a]
    assert (repo.max_pending_sort_order, repo.max_learned_sort_order) == (1, 0)


def test_shuffle_uses_common_batch_entry_and_keeps_other_state(repo, monkeypatch):
    entries = [repo.save(word(text)) for text in "abcd"]
    learned = repo.save(word("learned", learned=True))
    repo.batch_update_sort_orders({entries[-1].id: 50})
    monkeypatch.setattr("vocabulary.storage.random.shuffle", lambda items: items.reverse())
    calls = []
    original = repo.batch_update_sort_orders
    def tracked(updates):
        calls.append(updates)
        original(updates)
    monkeypatch.setattr(repo, "batch_update_sort_orders", tracked)
    repo.shuffle_pending()
    assert [(e.text, e.sort_order) for e in repo.list_entries()] == [
        ("d", 1), ("c", 2), ("b", 3), ("a", 4), ("learned", 1)]
    assert len(calls) == 1 and repo.get(learned.id) == learned
    assert repo.max_pending_sort_order == 50
    assert repo.save(word("new")).sort_order == 51


@pytest.mark.parametrize("order", [0, -1, True, 1.5, "1", None])
def test_invalid_json_order_rejected_before_write(repo, order):
    document = json.loads(serialize_entries([word("good"), word("bad")]))
    document["words"][1]["sort_order"] = order
    with pytest.raises(ValidationError):
        repo.import_entries(parse_entries(json.dumps(document)))
    assert not repo.list_entries()


def test_import_order_is_only_a_sort_key_even_for_large_values(repo):
    source = [word("later", order=2**80), word("earlier", order=2**70)]
    repo.import_entries(parse_entries(serialize_entries(source)))
    assert [(e.text, e.sort_order) for e in repo.list_entries()] == [("earlier", 1), ("later", 2)]


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("mixed", [False, True])
def test_import_stable_order_and_optional_legacy_field(repo, version, mixed):
    source = [word("a", order=20), word("b", order=10), word("c", order=10)]
    document = json.loads(serialize_entries(source))
    document["version"] = version
    if mixed:
        document["words"][1].pop("sort_order")
    repo.import_entries(parse_entries(json.dumps(document)))
    assert [(e.text, e.sort_order) for e in repo.list_entries()] == list(
        zip("abc" if mixed else "bca", (1, 2, 3)))


def test_import_new_first_then_click_order_and_repeated_names(repo, qtbot):
    old = repo.save(word("old"))
    result = repo.import_entries([word("OLD", order=1), word("fresh", order=3),
                                  word("Old", order=2)])
    assert [(e.text, e.sort_order) for e in repo.list_entries()] == [("old", 1), ("fresh", 2)]
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.choose(dialog.conflicts[1], True)
    current = dialog.conflicts[0].current
    assert current.id != old.id and current.text == "Old" and current.sort_order == 3
    assert dialog.cards[0][0].entry == current
    dialog.choose_all(True)
    latest = repo.list_entries()[-1]
    assert latest.text == "OLD" and latest.sort_order == 4 and latest.id != current.id
    assert dialog.replaced_ids == {latest.id} and dialog.replaced_names == {"old"}


def test_import_recreate_failure_restores_deleted_aggregate_and_counters(repo):
    a, b = repo.save(word("a")), repo.save(word("b"))
    result = repo.import_entries([word("A", learned=True), word("B", learned=True)])
    result.conflicts[-1].incoming.senses[0].translation = "FAIL"
    repo.connection.executescript("""CREATE TRIGGER fail_sense BEFORE INSERT ON senses
        WHEN NEW.translation='FAIL' BEGIN SELECT RAISE(ABORT,'FAIL'); END;""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_imports(result.conflicts)
    assert repo.list_entries() == [a, b]
    assert repo.max_learned_sort_order == 0


@pytest.mark.parametrize("key,expected", [(Qt.Key.Key_N, "n."), (Qt.Key.Key_V, "v."), (Qt.Key.Key_A, "adj.")])
@pytest.mark.parametrize("popup", [False, True])
@pytest.mark.parametrize("shift", [False, True])
def test_pos_letter_selection(qtbot, key, expected, popup, shift):
    combo = PosCombo()
    qtbot.addWidget(combo)
    combo.show()
    combo.activateWindow()
    combo.setFocus()
    qtbot.wait(20)
    if popup:
        combo.showPopup()
    qtbot.keyClick(combo.view() if popup else combo, key,
                   Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier)
    assert combo.currentData() == expected and not combo.view().isVisible()
    assert combo.hasFocus()


@pytest.mark.parametrize("related", [False, True])
@pytest.mark.parametrize("popup", [False, True])
def test_form_a_cycles_adjective_adverb_and_keeps_focus(window, qtbot, related, popup):
    if related:
        window.add_related()
    combo = window.blocks[-1].rows[0].pos
    combo.setFocus()
    for expected in ("adj.", "adv.", "adj.", "adv."):
        if popup:
            combo.showPopup()
        qtbot.keyClick(combo.view() if popup else combo, Qt.Key.Key_A)
        assert combo.currentData() == expected
        assert combo.hasFocus() and not combo.view().isVisible()
    qtbot.keyClick(combo, Qt.Key.Key_3)
    assert combo.currentData() == "adj."
    qtbot.keyClick(combo, Qt.Key.Key_4)
    assert combo.currentData() == "adv."
    qtbot.keyClick(combo, Qt.Key.Key_N)
    qtbot.keyClick(combo, Qt.Key.Key_A, Qt.KeyboardModifier.ShiftModifier)
    assert combo.currentData() == "adj."


def test_holding_a_does_not_repeat_toggle(window, qtbot):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    combo = window.blocks[0].rows[0].pos
    combo.setFocus()
    combo.set_pos("adj.")
    qtbot.keyClick(combo, Qt.Key.Key_A)
    assert combo.currentData() == "adv."
    for _ in range(3):
        QApplication.sendEvent(combo, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                              Qt.KeyboardModifier.NoModifier, "a", True))
    assert combo.currentData() == "adv."
    qtbot.keyClick(combo, Qt.Key.Key_A)
    assert combo.currentData() == "adj."


@pytest.mark.parametrize("query", [" RECORD ", "唱片", "RECORDER", "录音", "[literal].%_"])
def test_search_all_fields_and_filtered_export(window, repo, qtbot, tmp_path, monkeypatch, query):
    entry = repo.save(WordEntry("record", [Sense("n.", "记录；唱片")],
        [RelatedWord("recorder", [Sense("n.", "录音 [literal].%_")])]))
    repo.save(word("other"))
    window.refresh()
    window.search_box.setText(query)
    assert visible(window) == [entry.text]
    assert all(not row.hidden for row in window.model.rows)
    assert any(row.owner >= 0 for row in window.model.rows)
    assert not window.revealed  # The temporary projection must not change review state.
    assert not window.table.reorder_enabled and not window.table.shuffle_button.isEnabled()
    path = tmp_path / "filtered.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    window.export_words()
    assert read_entries(path) == [replace(entry, id=None)]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == 2 and document["words"][0]["sort_order"] == 1
    window.set_filter("learned")
    assert not visible(window) and window.search_box.text() == query
    assert not window.table.shuffle_button.isVisible()
    window.set_filter("all")
    assert visible(window) == [entry.text] and not window.table.show_drag_handle
    window.set_filter("pending")
    window.search_box.setText("   ")
    assert visible(window) == ["record", "other"] and window.table.reorder_enabled
    assert window.table.shuffle_button.isEnabled()
    assert all(row.hidden for row in window.model.rows)


@pytest.mark.parametrize("clear_text", ["", "   "])
def test_search_temporary_reveal_restores_mixed_states_across_pages(window, repo, qtbot, clear_text):
    hidden = repo.save(WordEntry("record-hidden", [Sense("n.", "遮住的翻译"), Sense("v.", "第二释义")],
                     [RelatedWord("child", [Sense("n.", "关联译文")])]))
    expanded = repo.save(word("record-expanded"))
    learned = repo.save(word("record-learned", learned=True))
    window.reveal(expanded.id, True)
    original = set(window.revealed)
    window.search_box.setText("record")
    assert visible(window) == [hidden.text, expanded.text]
    assert all(not row.hidden for row in window.model.rows)
    assert window.model.rowCount() == 4
    # Ctrl-click cannot conceal search results or alter the state restored afterwards.
    row = next(i for i, item in enumerate(window.model.rows) if item.entry.id == expanded.id)
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.ControlModifier,
                    pos=window.table.visualRect(window.model.index(row, 3)).center())
    assert window.revealed == original and all(not row.hidden for row in window.model.rows)
    window.set_filter("learned")
    assert visible(window) == [learned.text] and not window.model.rows[0].hidden
    window.search_box.setText("no-match")
    assert not window.model.rows and window.revealed == original
    window.search_box.setText("record")
    window.set_filter("all")
    assert all(not row.hidden for row in window.model.rows)
    window.search_box.setText(clear_text)
    assert window.revealed == original
    assert [row.entry.id for row in window.model.rows if row.hidden] == [hidden.id, learned.id]
    assert all(not row.hidden for row in window.model.rows if row.entry.id == expanded.id)


def test_search_keeps_results_expanded_when_global_visibility_changes(window, repo):
    entry = repo.save(word("record"))
    window.refresh()
    window.search_box.setText("record")
    window.set_global_hidden(False)
    assert not window.model.rows[0].hidden and window.revealed == {entry.id}
    window.set_global_hidden(True)
    assert not window.model.rows[0].hidden and not window.revealed
    window.search_box.clear()
    assert window.model.rows[0].hidden


def drag_handle(table, row):
    rect = table.visualRect(table.model().index(row, 4))
    return dict(table.action_rects(rect))["drag"].center()


def test_group_drag_preview_then_one_commit(window, repo, qtbot):
    entries = [repo.save(word(text)) for text in "abc"]
    # Expand the first group to multiple rows of different heights.
    first = replace(entries[0], senses=[Sense("n.", "第一行\n第二行"), Sense("v.", "动词")],
                    related=[RelatedWord("child", [Sense("n.", "关联")])])
    repo.save(first)
    window.revealed.add(first.id)
    window.refresh()
    table = window.table
    before = repo.list_entries()
    origin = drag_handle(table, 0)
    end = QPoint(origin.x(), table.rowViewportPosition(table.model().rowCount() - 1) + 28)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), end, delay=30)
    assert table._drag_active and table._drag_target == 2
    qtbot.wait(180)
    assert repo.list_entries() == before
    assert table._drag_groups[0][2] == 2 and table._drag_groups[0][3] > 70
    assert table._drag_positions[entries[1].id] == 0
    assert not table.grab().isNull()  # Exercise animation/preview painting.
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert visible(window) == ["b", "c", "a"]
    assert repo.get(first.id).sort_order == 4 and first.id in window.revealed
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == first.id


@pytest.mark.parametrize("cancel", ["escape", "outside", "same", "threshold"])
def test_drag_cancel_does_not_write(window, repo, qtbot, cancel):
    entries = [repo.save(word(text)) for text in "abc"]
    window.refresh()
    table = window.table
    origin = drag_handle(table, 0)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    if cancel == "threshold":
        destination = origin + QPoint(0, 1)
    elif cancel == "same":
        destination = origin + QPoint(-20, 0)
    else:
        destination = origin + QPoint(0, 80)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    if cancel == "escape":
        qtbot.keyClick(table, Qt.Key.Key_Escape)
    if cancel == "outside":
        destination = QPoint(origin.x(), -10)
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert repo.list_entries() == entries and not table._drag_active


def test_drag_edge_autoscroll_and_failure_restores_order(window, repo, qtbot, monkeypatch):
    entries = [repo.save(word(f"word{i}")) for i in range(70)]
    window.refresh()
    table = window.table
    origin = drag_handle(table, 0)
    destination = QPoint(origin.x(), table.viewport().height() - 3)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    qtbot.wait(100)
    assert table.verticalScrollBar().value() > 0
    def fail(*args):
        raise OSError("locked")
    monkeypatch.setattr(repo, "move_entry", fail)
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert repo.list_entries() == entries and visible(window) == [e.text for e in entries]
    assert window.toast.label.text() == "locked"


def test_search_blocks_programmatic_reorder_and_shuffle(window, repo, monkeypatch):
    entries = [repo.save(word(text)) for text in ("aaa", "aab")]
    window.refresh()
    window.search_box.setText("aa")
    def unexpected(*args):
        raise AssertionError("should not reorder while searching")
    monkeypatch.setattr(repo, "move_entry", unexpected)
    monkeypatch.setattr(repo, "shuffle_pending", unexpected)
    window.move_word(entries[0].id, 1)
    window.shuffle_words()
    assert repo.list_entries() == entries

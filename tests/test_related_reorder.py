from copy import deepcopy
from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository


def word(text="parent", learned=False):
    return WordEntry(text, [Sense("n.", "主词")],
                     [RelatedWord(name, [Sense("n.", name), Sense("v.", "补充\n释义")])
                      for name in ("child-a", "child-b", "child-c")], learned=learned)


def names(entry):
    return [child.text for child in entry.related]


def test_related_storage_changes_only_positions_and_survives_restart(repo):
    entry = repo.save(word())
    other = repo.save(word("other"))
    repo.prepare_study(StudyContext(date(2026, 11, 2), False))
    progress = repo.study_backup().records
    children = repo.connection.execute("SELECT id,word_id,text FROM related_words ORDER BY id").fetchall()
    master = tuple(repo.connection.execute("SELECT * FROM words WHERE id=?", (entry.id,)).fetchone())
    repo.move_related(entry.id, 0, 2)
    assert names(repo.get(entry.id)) == ["child-b", "child-c", "child-a"]
    assert list(repo.study_backup().records.items()) == list(progress.items())
    assert repo.get(other.id) == other
    assert children == repo.connection.execute("SELECT id,word_id,text FROM related_words ORDER BY id").fetchall()
    assert tuple(repo.connection.execute("SELECT * FROM words WHERE id=?", (entry.id,)).fetchone()) == master
    reopened = Repository(repo.path)
    try:
        assert names(reopened.get(entry.id)) == ["child-b", "child-c", "child-a"]
    finally:
        reopened.close()


def test_related_storage_failure_rolls_back_every_position(repo):
    entry = repo.save(word())
    repo.connection.execute("""CREATE TRIGGER reject_related BEFORE UPDATE ON related_words
                               WHEN NEW.position=1 BEGIN SELECT RAISE(ABORT,'locked'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.move_related(entry.id, 0, 2)
    assert repo.get(entry.id) == entry
    for source, target in [(-1, 0), (0, 3), (True, 1)]:
        with pytest.raises(ValueError):
            repo.move_related(entry.id, source, target)


@pytest.fixture(params=["pending", "all", "learned", "study17", "search"])
def related_list(window, repo, qtbot, monkeypatch, request):
    entry = repo.save(word(learned=request.param == "learned"))
    other = repo.save(word("other", learned=request.param == "learned"))
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 2), False)))
    window.set_filter("pending" if request.param == "search" else request.param)
    table = window.current_table()
    if request.param == "search":
        window.search_box.setText("child")
    else:
        table.revealRequested.emit(entry.id, True)
    qtbot.wait(20)
    yield table, entry, other
    table.cancel_drag()


def child_row(table, entry_id, owner):
    return next(i for i, row in enumerate(table.model().rows)
                if row.entry.id == entry_id and row.owner == owner and row.sense == 0)


def handle(table, entry_id, owner):
    number = child_row(table, entry_id, owner)
    rect = table.visualRect(table.model().index(number, 1))
    return QPoint(rect.left() + 16, rect.top() + table.model().rows[number].top_padding + 8)


def last_point(table, entry_id, owner):
    number = child_row(table, entry_id, owner)
    row = table.model().rows[number]
    last = number + len(row.word.senses) - 1
    return QPoint(handle(table, entry_id, owner).x(), table.visualRect(table.model().index(last, 1)).bottom() - 2)


def test_list_drag_related_group_moves_and_preserves_learning(related_list, window, repo, qtbot, monkeypatch):
    table, entry, other = related_list
    before = repo.study_backup().records
    monkeypatch.setattr(repo, "prepare_study", lambda *args: pytest.fail("reorder must not draw words"))
    origin, destination = handle(table, entry.id, 0), last_point(table, entry.id, 2)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    assert table._drag_active and table._drag_target == 2
    assert names(repo.get(entry.id)) == names(entry)
    assert len(table._drag_groups) == 3 and table._drag_groups[0][2] - table._drag_groups[0][1] == 1
    assert not table.viewport().grab().isNull()
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert names(repo.get(entry.id)) == ["child-b", "child-c", "child-a"]
    assert repo.get(other.id) == other
    assert repo.study_backup().records == before
    assert [row.word.text for row in table.model().rows if row.entry.id == entry.id and row.owner >= 0
            and row.sense == 0] == ["child-b", "child-c", "child-a"]


@pytest.mark.parametrize("cancel", ["escape", "outside", "other-parent", "same", "threshold", "failure"])
def test_list_drag_cancel_and_failure_keep_original_order(related_list, window, repo, qtbot, monkeypatch, cancel):
    table, entry, other = related_list
    origin, destination = handle(table, entry.id, 0), last_point(table, entry.id, 2)
    if cancel in ("same", "threshold"):
        destination = origin + QPoint(15 if cancel == "same" else 1, 0)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    if cancel == "escape":
        qtbot.keyClick(table, Qt.Key.Key_Escape)
    elif cancel == "outside":
        destination = QPoint(-5, destination.y())
    elif cancel == "other-parent":
        destination = origin - QPoint(0, 40)  # Main-word area is outside the sibling scope.
    elif cancel == "failure":
        def fail(*args):
            raise OSError("locked")
        monkeypatch.setattr(repo, "move_related", fail)
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert repo.get(entry.id) == entry and repo.get(other.id) == other
    assert not table._drag_active


def test_list_related_drag_preserves_independent_draft(window, repo, qtbot, monkeypatch):
    entry = repo.save(word())
    window.reveal(entry.id, True)
    window.blocks[0].word.setText("draft")
    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("drag must not process draft"))
    qtbot.mousePress(window.table.viewport(), Qt.MouseButton.LeftButton, pos=handle(window.table, entry.id, 0))
    assert window.table._drag_candidate is not None and window.blocks[0].word.text() == "draft"
    window.table.cancel_drag()


def test_related_drag_after_inline_save_survives_deferred_refresh(window, repo, qtbot):
    entry = repo.save(word())
    window.reveal(entry.id, True)
    table = window.table
    table.edit(window.model.index(0, 3))
    table.delegate.editor.setPlainText("edited")
    origin, destination = handle(table, entry.id, 0), last_point(table, entry.id, 2)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    qtbot.wait(70)
    assert table._drag_active
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert names(repo.get(entry.id)) == ["child-b", "child-c", "child-a"]
    assert repo.get(entry.id).senses[0].translation == "edited"


def test_single_related_arrow_does_not_drag_or_copy(window, repo, qtbot):
    entry = word()
    entry.related = entry.related[:1]
    entry = repo.save(entry)
    window.reveal(entry.id, True)
    QApplication.clipboard().setText("unchanged")
    origin = handle(window.table, entry.id, 0)
    qtbot.mousePress(window.table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(window.table.viewport(), origin + QPoint(0, 30))
    qtbot.mouseRelease(window.table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    assert not window.table.dragging and QApplication.clipboard().text() == "unchanged"
    assert repo.get(entry.id) == entry


def test_form_reorder_updates_tab_order_and_cancel_does_not_save(window, repo, qtbot):
    entry = repo.save(word())
    window.reset_form(entry)
    blocks = list(window.blocks)
    window.move_form_related(0, 2)
    assert window.blocks[-1] is blocks[1]
    assert repo.get(entry.id) == entry
    qtbot.wait(10)
    last_input = blocks[0].input_order()[-1]
    last_input.setFocus()
    qtbot.keyClick(last_input, Qt.Key.Key_Tab)
    assert blocks[2].word.hasFocus()
    qtbot.keyClick(blocks[2].word, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert last_input.hasFocus()
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().confirm_button.click())
    window.cancel_form()
    assert repo.get(entry.id) == entry and not window.is_dirty()


@pytest.mark.parametrize("editing", [False, True])
def test_form_drag_reuses_widgets_and_saves_new_order(window, repo, qtbot, editing):
    entry = repo.save(word()) if editing else word()
    window.reset_form(entry)
    qtbot.wait(30)
    blocks = list(window.blocks)
    handle_widget = blocks[1].indicator
    window.form_scroll.ensureWidgetVisible(handle_widget)
    qtbot.wait(20)
    viewport = window.form_scroll.viewport()
    origin = handle_widget.rect().center()
    destination = QPoint(20, viewport.height() - 3)
    qtbot.mousePress(handle_widget, Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(handle_widget, handle_widget.mapFromGlobal(viewport.mapToGlobal(destination)), delay=20)
    assert window.form_drag.active
    assert window.shortcuts.suspended
    bar = window.form_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.value() == bar.maximum(), timeout=2000)
    assert not window.form_drag.preview.grab().isNull()
    assert window.blocks == blocks  # The preview must not mutate drafts.
    qtbot.mouseRelease(handle_widget, Qt.MouseButton.LeftButton,
                       pos=handle_widget.mapFromGlobal(viewport.mapToGlobal(destination)))
    assert window.blocks == [blocks[0], blocks[2], blocks[3], blocks[1]]
    assert not window.shortcuts.suspended
    assert window.is_dirty()
    assert window.blocks[-1].rows[1].translation.toPlainText() == "补充\n释义"
    assert window._save_form()
    saved = repo.get(entry.id) if editing else repo.list_entries()[0]
    assert names(saved) == ["child-b", "child-c", "child-a"]


@pytest.mark.parametrize("cancel", ["escape", "outside", "same", "threshold"])
def test_form_drag_can_cancel_without_changing_draft(window, qtbot, cancel):
    window.reset_form(word())
    qtbot.wait(30)
    handle_widget = window.blocks[1].indicator
    window.form_scroll.ensureWidgetVisible(handle_widget)
    qtbot.wait(20)
    before = deepcopy(window.form_value())
    origin = handle_widget.rect().center()
    destination = origin + QPoint(15 if cancel != "threshold" else 1, 0)
    qtbot.mousePress(handle_widget, Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(handle_widget, destination, delay=20)
    if cancel == "escape":
        qtbot.keyClick(window, Qt.Key.Key_Escape)
    elif cancel == "outside":
        destination = handle_widget.mapFromGlobal(window.form_scroll.viewport().mapToGlobal(QPoint(-4, -4)))
    qtbot.mouseRelease(handle_widget, Qt.MouseButton.LeftButton, pos=destination)
    assert window.form_value() == before and not window.form_drag.active

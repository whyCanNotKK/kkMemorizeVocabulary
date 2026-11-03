from copy import deepcopy
from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.pos_picker import PosPicker
from vocabulary.storage import Repository


def word(text="parent", learned=False):
    senses = [Sense("n.", "first"), Sense("v.", "second\nline"), Sense("adj.", "third")]
    return WordEntry(text, deepcopy(senses),
                     [RelatedWord("child", deepcopy(senses)), RelatedWord("sibling", [Sense("n.", "only")])],
                     learned=learned)


def owner_word(entry, owner):
    return entry if owner == -1 else entry.related[owner]


@pytest.mark.parametrize("owner", [-1, 0])
def test_storage_reorders_only_sense_positions_and_survives_restart(repo, owner):
    entry = repo.save(word())
    other = repo.save(word("other"))
    repo.prepare_study(StudyContext(date(2026, 11, 2), False))
    progress = repo.study_backup().records
    rows = repo.connection.execute("SELECT id,word_id,related_id,pos,translation FROM senses ORDER BY id").fetchall()
    masters = repo.connection.execute("SELECT * FROM words ORDER BY id").fetchall()
    children = repo.connection.execute("SELECT * FROM related_words ORDER BY id").fetchall()
    repo.move_sense(entry.id, owner, 0, 2)
    expected = deepcopy(entry)
    senses = owner_word(expected, owner).senses
    senses.append(senses.pop(0))
    assert repo.get(entry.id) == expected and repo.get(other.id) == other
    assert rows == repo.connection.execute("SELECT id,word_id,related_id,pos,translation FROM senses ORDER BY id").fetchall()
    assert masters == repo.connection.execute("SELECT * FROM words ORDER BY id").fetchall()
    assert children == repo.connection.execute("SELECT * FROM related_words ORDER BY id").fetchall()
    assert repo.study_backup().records == progress
    reopened = Repository(repo.path)
    try:
        assert reopened.get(entry.id) == expected
    finally:
        reopened.close()


@pytest.mark.parametrize("owner", [-1, 0])
def test_storage_reorder_failure_rolls_back_all_positions(repo, owner):
    entry = repo.save(word())
    repo.connection.execute("""CREATE TRIGGER reject_sense BEFORE UPDATE ON senses
                               WHEN NEW.position=1 BEGIN SELECT RAISE(ABORT,'locked'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.move_sense(entry.id, owner, 0, 2)
    assert repo.get(entry.id) == entry
    for bad_owner, source, target in [(-2, 0, 1), (True, 0, 1), (2, 0, 1),
                                      (owner, -1, 0), (owner, 0, 3), (owner, True, 1)]:
        with pytest.raises(ValueError):
            repo.move_sense(entry.id, bad_owner, source, target)
    assert repo.get(entry.id) == entry


@pytest.fixture(params=["pending", "all", "learned", "study17", "search"])
def sense_list(window, repo, qtbot, monkeypatch, request):
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
    table.delegate.cancel()
    table.cancel_drag()


def sense_index(table, entry_id, owner, sense):
    number = next(i for i, row in enumerate(table.model().rows)
                  if (row.entry.id, row.owner, row.sense) == (entry_id, owner, sense))
    return table.model().index(number, 2)


def handle(table, entry_id, owner, sense=0):
    index = sense_index(table, entry_id, owner, sense)
    return table.delegate.pos_rect(index, table.visualRect(index)).center().toPoint()


@pytest.mark.parametrize("owner", [-1, 0])
@pytest.mark.parametrize("source,target", [(0, 2), (2, 0)])
def test_drag_sense_in_each_list_preserves_other_words_and_learning(
        sense_list, window, repo, qtbot, monkeypatch, owner, source, target):
    table, entry, other = sense_list
    progress = repo.study_backup().records
    monkeypatch.setattr(repo, "prepare_study", lambda *args: pytest.fail("sorting must not draw words"))
    origin = handle(table, entry.id, owner, source)
    rect = table.visualRect(sense_index(table, entry.id, owner, target))
    destination = QPoint(origin.x(), rect.bottom() - 2 if target > source else rect.top() + 1)
    QApplication.clipboard().setText("keep clipboard")
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    assert table.dragging and table._drag_target == target
    assert len(table._drag_groups) == 3 and all(group[1] == group[2] for group in table._drag_groups)
    assert not table.viewport().grab().isNull()
    assert repo.get(entry.id) == entry
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    expected = deepcopy(entry)
    senses = owner_word(expected, owner).senses
    senses.insert(target, senses.pop(source))
    assert repo.get(entry.id) == expected and repo.get(other.id) == other
    assert repo.study_backup().records == progress
    assert [row.word.senses[row.sense] for row in table.model().rows
            if row.entry.id == entry.id and row.owner == owner] == senses
    assert QApplication.clipboard().text() == "keep clipboard"


@pytest.mark.parametrize("owner", [-1, 0])
@pytest.mark.parametrize("cancel", ["escape", "outside", "other-word", "same", "threshold", "failure"])
def test_sense_drag_cancel_or_failure_preserves_order(sense_list, repo, qtbot, monkeypatch, owner, cancel):
    table, entry, other = sense_list
    origin = handle(table, entry.id, owner)
    last = table.visualRect(sense_index(table, entry.id, owner, 2))
    destination = QPoint(origin.x(), last.bottom() - 2)
    if cancel in ("same", "threshold"):
        destination = origin + QPoint(15 if cancel == "same" else 1, 0)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    if cancel == "escape":
        qtbot.keyClick(table, Qt.Key.Key_Escape)
    elif cancel == "outside":
        destination.setX(-5)
    elif cancel == "other-word":
        destination = handle(table, entry.id, 0 if owner == -1 else -1)
    elif cancel == "failure":
        def fail(*args):
            raise OSError("locked")
        monkeypatch.setattr(repo, "move_sense", fail)
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert repo.get(entry.id) == entry and repo.get(other.id) == other
    assert not table.dragging


@pytest.mark.parametrize("owner", [-1, 0])
def test_click_and_double_click_pos_keep_existing_edit_behavior(sense_list, repo, qtbot, owner):
    table, entry, other = sense_list
    point = handle(table, entry.id, owner)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not table.dragging and table.delegate.editor is None
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert isinstance(table.delegate.editor, PosPicker) and table.delegate.editor.isVisible()
    assert not table.dragging
    qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Escape)
    assert repo.get(entry.id) == entry


def test_sense_drag_after_inline_save_keeps_independent_form(window, repo, qtbot, monkeypatch):
    entry = repo.save(word())
    window.reveal(entry.id, True)
    window.blocks[0].word.setText("independent draft")
    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("sorting must keep draft"))
    table = window.table
    table.edit(window.model.index(0, 3))
    table.delegate.editor.setPlainText("edited before drag")
    origin = handle(table, entry.id, -1)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    last = table.visualRect(sense_index(table, entry.id, -1, 2))
    destination = QPoint(origin.x(), last.bottom() - 2)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    qtbot.wait(70)
    assert table.dragging
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert [sense.translation for sense in repo.get(entry.id).senses] == ["second\nline", "third", "edited before drag"]
    assert window.blocks[0].word.text() == "independent draft"

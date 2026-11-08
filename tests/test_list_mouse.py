from copy import deepcopy
from datetime import date

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QPlainTextEdit

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.pos_picker import PosPicker


@pytest.fixture(params=["pending", "all", "learned", "study17", "search"])
def mouse_list(window, repo, qtbot, monkeypatch, request):
    senses = [Sense("n.", "first definition\n第二行释义"), Sense("v.", "second definition")]
    entry = repo.save(WordEntry("vocabulary", deepcopy(senses),
                               [RelatedWord("vocabularies", deepcopy(senses))],
                               learned=request.param == "learned"))
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 2), False)))
    window.set_filter("pending" if request.param == "search" else request.param)
    table = window.current_table()
    if request.param == "search":
        window.search_box.setText("definition")
    else:
        table.revealRequested.emit(entry.id, True)
    qtbot.wait(20)
    QApplication.clipboard().setText("keep clipboard")
    yield table, entry
    table.delegate.cancel()
    table.cancel_drag()


def field(table, owner, column, sense=0):
    number = next(i for i, row in enumerate(table.model().rows) if (row.owner, row.sense) == (owner, sense))
    index = table.model().index(number, column)
    rect = table.visualRect(index)
    row = index.data(Qt.ItemDataRole.UserRole)
    if column == 2:
        point = table.delegate.pos_rect(index, rect).center().toPoint()
    else:
        point = rect.topLeft() + QPoint((31 if owner >= 0 else 15) if column == 1 else 15,
                                       row.top_padding + 7)
    return index, point


@pytest.mark.parametrize("owner", [-1, 0])
@pytest.mark.parametrize("column", [1, 2, 3])
def test_single_click_copies_only_word_or_definition(mouse_list, qtbot, owner, column):
    table, entry = mouse_list
    index, point = field(table, owner, column)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert QApplication.clipboard().text() == "keep clipboard"
    qtbot.wait(QApplication.doubleClickInterval() + 60)
    assert QApplication.clipboard().text() == (index.data() if column != 2 else "keep clipboard")
    assert table.delegate.editor is None and not table.dragging


# Cover every pair of owner, field and gesture in every page without the
# redundant full three-way Cartesian product of the shared editor path.
@pytest.mark.parametrize("owner,column,gesture", [
    (-1, 1, "double"), (0, 1, "right"),
    (-1, 2, "right"), (0, 2, "double"),
    (-1, 3, "double"), (0, 3, "right"),
])
def test_double_or_right_click_edits_without_copying(mouse_list, window, repo, qtbot, owner, column, gesture):
    table, entry = mouse_list
    index, point = field(table, owner, column)
    before_progress = list(repo.study_backup().records.values())
    draft = window.form_value()
    if gesture == "double":
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    else:
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=point)
    editor = table.delegate.editor
    assert isinstance(editor, {1: QLineEdit, 2: PosPicker, 3: QPlainTextEdit}[column])
    assert editor.isVisible() and not table.dragging
    qtbot.wait(QApplication.doubleClickInterval() + 60)
    assert QApplication.clipboard().text() == "keep clipboard"
    assert not window.toast.isVisible()
    if column == 2:
        qtbot.keyClick(editor.view(), Qt.Key.Key_3)
        expected = "adj."
    else:
        expected = "edited name" if column == 1 else "edited definition\nnew line"
        editor.setText(expected) if column == 1 else editor.setPlainText(expected)
        qtbot.keyClick(editor, Qt.Key.Key_Return)
    actual = repo.get(entry.id)
    word = actual if owner == -1 else actual.related[owner]
    assert (word.text if column == 1 else word.senses[0].pos if column == 2 else word.senses[0].translation) == expected
    assert list(repo.study_backup().records.values()) == before_progress and window.form_value() == draft


@pytest.mark.parametrize("owner", [-1, 0])
@pytest.mark.parametrize("column", [1, 2, 3])
def test_holding_and_dragging_reorders_only_pos(mouse_list, repo, qtbot, owner, column):
    table, entry = mouse_list
    index, point = field(table, owner, column)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    if column == 2:
        last, _ = field(table, owner, column, sense=1)
        destination = QPoint(point.x(), table.visualRect(last).bottom() - 2)
    else:
        destination = point + QPoint(QApplication.startDragDistance() + 5, 0)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    assert table.dragging == (column == 2)
    qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    qtbot.wait(QApplication.doubleClickInterval() + 60)
    expected = deepcopy(entry)
    if column == 2:
        word = expected if owner == -1 else expected.related[owner]
        word.senses.reverse()
    assert repo.get(entry.id) == expected
    assert QApplication.clipboard().text() == "keep clipboard" and table.delegate.editor is None


def test_right_click_keeps_invalid_editor_and_independent_draft(window, repo, qtbot):
    entry = repo.save(WordEntry("word", [Sense("n.", "definition")],
                                [RelatedWord("child", [Sense("n.", "child")])]))
    window.reveal(entry.id, True)
    window.blocks[0].word.setText("independent draft")
    table = window.table
    table.edit(window.model.index(0, 1))
    editor = table.delegate.editor
    editor.setText("")
    index, point = field(table, -1, 3)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=point)
    assert table.delegate.editor is editor and editor.text() == ""
    assert repo.get(entry.id) == entry and window.blocks[0].word.text() == "independent draft"


def test_right_click_edits_the_same_sibling_after_removing_a_related_word(window, repo, qtbot):
    entry = repo.save(WordEntry("main", [Sense("n.", "main")],
                                [RelatedWord(name, [Sense("n.", name)]) for name in ("first", "second")]))
    window.reveal(entry.id, True)
    table = window.table
    table.edit(table._entry_index(entry.id, 0))
    table.delegate.editor.setText(" ")
    _, point = field(table, 1, 1)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=point)
    assert repo.get(entry.id).related == entry.related[1:]
    assert table.delegate.editor.text() == "second"


def test_edit_button_keeps_its_target_when_clearing_the_word_above(window, repo, qtbot):
    first, second = [repo.save(WordEntry(name, [Sense("n.", name)])) for name in ("first", "second")]
    window.refresh()
    table = window.table
    table.edit(table._entry_index(first.id))
    table.delegate.editor.clear()
    index = table._entry_index(second.id, column=table.action_column)
    actions = dict(table.action_rects(table.visualRect(index), index.data(Qt.ItemDataRole.UserRole)))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=actions["edit"].center())
    assert repo.list_entries() == [second]
    assert window.edit_id == second.id


@pytest.mark.parametrize("owner", [-1, 0])
@pytest.mark.parametrize("column", [1, 3])
def test_right_edit_never_opens_native_context_menu(mouse_list, qtbot, owner, column):
    table, entry = mouse_list
    index, point = field(table, owner, column)
    original = index.data()
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=point)
    editor = table.delegate.editor
    target = editor.viewport() if isinstance(editor, QPlainTextEdit) else editor
    menus = []

    def close_unexpected_menu():
        popup = QApplication.activePopupWidget()
        if isinstance(popup, QMenu):
            menus.append(True)
            popup.close()

    # Windows sends a separate context-menu event after the initiating right-click.
    # Repeat after editing begins as well; auto-close a menu so regressions cannot hang.
    for _ in range(2):
        local = QPoint(5, 5)
        QTimer.singleShot(20, close_unexpected_menu)
        QApplication.sendEvent(target, QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, local, target.mapToGlobal(local)))
        qtbot.wait(30)
        assert menus == [] and table.delegate.editor is editor
        assert QApplication.clipboard().text() == "keep clipboard"
    editor.setFocus()
    qtbot.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(editor, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == original
    QApplication.clipboard().setText("pasted text")
    qtbot.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert (editor.toPlainText() if column == 3 else editor.text()) == "pasted text"
    qtbot.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert (editor.toPlainText() if column == 3 else editor.text()) == original


@pytest.mark.parametrize("column", [2, 3])
def test_hidden_answers_cannot_be_copied_or_right_edited(window, repo, qtbot, column):
    entry = repo.save(WordEntry("word", [Sense("n.", "secret definition")]))
    window.refresh()
    table = window.table
    rect = table.visualRect(window.model.index(0, column))
    point = QPoint(rect.right() - 5, rect.top() + 18)
    QApplication.clipboard().setText("keep clipboard")
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=point)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qtbot.wait(QApplication.doubleClickInterval() + 60)
    assert table.delegate.editor is None and window.model.rows[0].hidden
    assert QApplication.clipboard().text() == "keep clipboard" and repo.get(entry.id) == entry

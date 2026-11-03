import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry


@pytest.fixture(params=["pending", "all", "learned"])
def results(window, repo, qtbot, request):
    entries = [repo.save(WordEntry(
        f"match-{i}", [Sense("n.", "释义"), Sense("v.", "补充")],
        [RelatedWord("child", [Sense("n.", "关联")])], learned=request.param == "learned"))
        for i in range(25)]
    window.set_filter(request.param)
    window.search_box.setText("match")
    qtbot.wait(20)
    return entries


def test_search_down_selects_first_result_despite_old_selection_and_scroll(window, qtbot, results):
    table, search = window.table, window.search_box
    table.select_entry(results[-1].id, scroll=True)
    assert table.verticalScrollBar().value() > 0
    search.setFocus()
    search.setCursorPosition(2)
    qtbot.keyClick(search, Qt.Key.Key_Down)
    assert table.hasFocus()
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == results[0].id
    assert {index.data(Qt.ItemDataRole.UserRole).entry.id
            for index in table.selectionModel().selectedRows()} == {results[0].id}
    assert table.visualRect(table.currentIndex()).intersects(table.viewport().rect())
    qtbot.keyClick(table, Qt.Key.Key_Up)
    assert search.hasFocus() and search.cursorPosition() == len(search.text())
    qtbot.keyClicks(search, "X")
    assert search.text() == "matchX"


def test_up_from_first_group_related_row_returns_to_search(window, qtbot, results):
    table = window.table
    related = next(i for i, row in enumerate(window.model.rows)
                   if row.entry.id == results[0].id and row.owner == 0)
    table.setCurrentIndex(window.model.index(related, 3))
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Up)
    assert window.search_box.hasFocus()


def test_up_from_second_result_stops_at_first_before_returning(window, qtbot, results):
    table = window.table
    table.select_entry(results[1].id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Up)
    assert table.hasFocus()
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == results[0].id
    qtbot.keyClick(table, Qt.Key.Key_W)
    assert table.hasFocus()
    qtbot.keyClick(table, Qt.Key.Key_Up)
    assert window.search_box.hasFocus()


def test_search_without_results_keeps_focus(window, qtbot):
    window.search_box.setText("absent")
    window.search_box.setFocus()
    qtbot.keyClick(window.search_box, Qt.Key.Key_Down)
    assert window.search_box.hasFocus()


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_search_down_enters_list_but_up_stays_in_list(window, repo, qtbot, query):
    entry = repo.save(WordEntry("word", [Sense("n.", "词")]))
    window.refresh()
    window.search_box.setText(query)
    window.search_box.setFocus()
    qtbot.keyClick(window.search_box, Qt.Key.Key_Down)
    assert window.table.hasFocus()
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id
    qtbot.keyClick(window.table, Qt.Key.Key_Up)
    assert window.table.hasFocus()


def test_modified_arrows_and_inline_editor_keep_their_scope(window, qtbot, results):
    search, table = window.search_box, window.table
    search.setFocus()
    for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier,
                     Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.MetaModifier):
        qtbot.keyClick(search, Qt.Key.Key_Down, modifier)
        assert search.hasFocus()
    table.select_entry(results[0].id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)
    assert table.hasFocus()
    table.edit(window.model.index(0, 3))
    editor = table.delegate.editor
    editor.setPlainText("未提交")
    qtbot.keyClick(editor, Qt.Key.Key_Up)
    assert table.delegate.editor is editor and editor.hasFocus()
    # Even a search-field key cannot pull focus from a pending editor.
    search.setFocus()
    qtbot.keyClick(search, Qt.Key.Key_Down)
    assert search.hasFocus() and table.delegate.editor is editor
    table.delegate.cancel()

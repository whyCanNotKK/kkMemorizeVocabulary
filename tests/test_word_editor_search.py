from datetime import date
import shiboken6

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.word_name_editor import WordNameEditor


@pytest.fixture(params=["pending", "all", "learned", "study17"])
def word_list(request, window, repo, monkeypatch):
    entry = repo.save(WordEntry("record", [Sense("n.", "记录")],
                               [RelatedWord("recorder", [Sense("n.", "录音机")], "/child/")],
                               learned=request.param == "learned", phonetic="/main/"))
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter(request.param)
    table = window.current_table()
    if request.param == "study17":
        table.set_entry_revealed(entry.id, True)
    else:
        window.reveal(entry.id, True)
    return table, entry


@pytest.mark.parametrize("owner", [-1, 0])
@pytest.mark.parametrize("gesture", ["double", "right"])
def test_whole_cell_edits_word_and_phonetic_atomically(word_list, window, repo, qtbot, owner, gesture):
    table, entry = word_list
    index = table.model().index(0 if owner == -1 else 1, 1)
    rect = table.visualRect(index)
    point = QPoint(rect.right() - 3, rect.top() + 2)
    draft = window.form_value()
    progress = repo.study_backup().records
    if gesture == "double":
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    else:
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=point)
    editor = table.delegate.editor
    assert isinstance(editor, WordNameEditor) and editor.panel.isVisible()
    assert editor.phonetic.text() == ("/main/" if owner == -1 else "/child/")
    assert editor.panel.y() > editor.y()
    editor.setText("renamed")
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    qtbot.wait(10)
    assert editor.phonetic.hasFocus() and table.delegate.editor is editor
    assert repo.get(entry.id) == entry
    editor.phonetic.setText(" /changed/ ")
    qtbot.keyClick(editor.phonetic, Qt.Key.Key_Return)
    assert table.delegate.editor is None and not editor.panel.isVisible()
    saved = repo.get(entry.id)
    target = saved if owner == -1 else saved.related[owner]
    assert target.text == "renamed" and target.phonetic == "/changed/"
    assert window.form_value() == draft
    assert list(repo.study_backup().records.values()) == list(progress.values())


@pytest.mark.parametrize("owner", [-1, 0])
def test_click_anywhere_in_word_cell_copies_word_including_phonetic(word_list, qtbot, owner):
    table, entry = word_list
    index = table.model().index(0 if owner == -1 else 1, 1)
    table.setCurrentIndex(index)
    rect = table.visualRect(index)
    QApplication.clipboard().setText("unchanged")
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                    pos=QPoint(rect.right() - 3, rect.top() + 2))
    qtbot.waitUntil(lambda: QApplication.clipboard().text() == index.data())
    assert table.delegate.editor is None


@pytest.mark.parametrize("finish", ["escape", "focus", "shortcut", "failure"])
def test_phonetic_focus_lifecycle_and_failed_save(word_list, window, repo, qtbot, finish):
    table, entry = word_list
    table.edit(table.model().index(0, 1))
    editor = table.delegate.editor
    editor.phonetic.setFocus()
    qtbot.wait(10)
    editor.phonetic.setText("/draft/")
    qtbot.keyClick(editor.phonetic, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert editor.hasFocus() and table.delegate.editor is editor
    editor.phonetic.setFocus()
    if finish == "escape":
        qtbot.keyClick(editor.phonetic, Qt.Key.Key_Escape)
        assert repo.get(entry.id) == entry and table.delegate.editor is None
    elif finish == "failure":
        repo.connection.execute("""CREATE TRIGGER fail_name BEFORE UPDATE ON words
            BEGIN SELECT RAISE(ABORT,'failed'); END""")
        qtbot.keyClick(editor.phonetic, Qt.Key.Key_Return)
        assert table.delegate.editor is editor and editor.panel.isVisible()
        assert editor.phonetic.text() == "/draft/" and repo.get(entry.id) == entry
        assert not window.set_filter("all")
    else:
        if finish == "focus":
            window.settings_button.setFocus()
            qtbot.waitUntil(lambda: table.delegate.editor is None)
        else:
            qtbot.keyClick(editor.phonetic, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        assert repo.get(entry.id).phonetic == "/draft/" and table.delegate.editor is None
    if finish != "failure":
        assert not shiboken6.isValid(editor.panel) or not editor.panel.isVisible()


@pytest.mark.parametrize("page", ["pending", "all", "learned"])
@pytest.mark.parametrize("clear", ["backspace", "button"])
def test_query_changes_scroll_to_selected_word_without_reordering(window, repo, qtbot, page, clear):
    for i in range(65):
        repo.save(WordEntry(f"a-{i:02}", [Sense("n.", "释义")], learned=page == "learned"))
    repo.save(WordEntry("abcde-first", [Sense("n.", "释义")], learned=page == "learned"))
    chosen = repo.save(WordEntry("abcde-second", [Sense("n.", "释义")], learned=page == "learned"))
    before = repo.list_entries()
    window.set_filter(page)
    window.search_box.setText("abcde")
    window.table.select_entry(chosen.id)
    window.search_box.setFocus()
    if clear == "backspace":
        for _ in range(5):
            qtbot.keyClick(window.search_box, Qt.Key.Key_Backspace)
            current = window.table.currentIndex()
            assert current.data(Qt.ItemDataRole.UserRole).entry.id == chosen.id
            assert window.table.visualRect(current).intersects(window.table.viewport().rect())
            assert window.search_box.hasFocus()
    else:
        # Exercise QLineEdit's native clear button through its actual click.
        from PySide6.QtWidgets import QToolButton
        button = next(button for button in window.search_box.findChildren(QToolButton) if button.isVisible())
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        assert not window.search_box.text()
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == chosen.id
    assert window.search_box.hasFocus()
    assert window.table.verticalScrollBar().value() > 0
    assert repo.list_entries() == before


@pytest.mark.parametrize("page", ["pending", "all", "learned"])
@pytest.mark.parametrize("next_query", ["ab", "no-match"])
@pytest.mark.parametrize("return_query", ["", "cr"])
def test_search_forgets_selected_word_once_it_stops_matching(
        window, repo, qtbot, page, next_query, return_query):
    for number in range(45):
        repo.save(WordEntry(f"ab-{number}", [Sense("n.", "释义")], learned=page == "learned"))
    entry = repo.save(WordEntry("crane", [Sense("n.", "鹤")], learned=page == "learned"))
    before = repo.list_entries()
    window.set_filter(page)
    window.search_box.setText("cr")
    window.table.select_entry(entry.id)
    window.search_box.setFocus()
    window.search_box.setText(next_query)
    assert not window.table.currentIndex().isValid()
    assert not window.table.selectionModel().hasSelection()
    window.search_box.setText(return_query)
    qtbot.wait(20)
    assert not window.table.currentIndex().isValid()
    assert not window.table.selectionModel().hasSelection()
    assert window.table.verticalScrollBar().value() == 0
    assert window.search_box.hasFocus()
    assert repo.list_entries() == before
    # A fresh explicit selection establishes a new target for subsequent edits.
    window.table.select_entry(entry.id)
    window.search_box.setText("c")
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id


@pytest.mark.parametrize("page", ["pending", "all", "learned"])
def test_new_search_selection_replaces_the_discarded_target(window, repo, page):
    first = repo.save(WordEntry("crane", [Sense("n.", "鹤")], learned=page == "learned"))
    second = repo.save(WordEntry("about", [Sense("prep.", "关于")], learned=page == "learned"))
    window.set_filter(page)
    window.search_box.setText("cr")
    window.table.select_entry(first.id)
    window.search_box.setText("ab")
    window.table.select_entry(second.id)
    window.search_box.clear()
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == second.id


def test_form_columns_are_horizontal_aligned_and_sized(window, repo, qtbot):
    entry = repo.save(WordEntry("record", [Sense("n.", "记录")],
                               [RelatedWord("child", [Sense("n.", "关联")])]))
    window.reset_form(entry)
    qtbot.wait(20)
    for block in window.blocks:
        word, ipa, pos, translation = block.word, block.phonetic, block.rows[0].pos, block.rows[0].translation
        points = [field.mapTo(window, QPoint()) for field in (word, ipa, pos, translation)]
        assert len({point.y() for point in points}) == 1
        assert points[0].x() < points[1].x() < points[2].x() < points[3].x()
        assert word.width() == (144 if not block.related else 122)
        assert ipa.width() == 108 and pos.width() == 78
        option = QStyleOptionComboBox()
        pos.initStyleOption(option)
        area = pos.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                         QStyle.SubControl.SC_ComboBoxEditField, pos)
        assert area.width() >= max(pos.fontMetrics().horizontalAdvance(text)
                                   for text in ("选择词性", "interj."))

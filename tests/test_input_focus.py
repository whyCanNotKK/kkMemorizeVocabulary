from datetime import date

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.shortcuts import SettingsDialog


def caret_at_end(editor):
    if isinstance(editor, QLineEdit):
        return editor.cursorPosition() == len(editor.text()) and not editor.hasSelectedText()
    cursor = editor.textCursor()
    return cursor.atEnd() and not cursor.hasSelection()


@pytest.mark.parametrize("field", ["search", "word", "translation", "related", "related_translation"])
def test_focusing_inputs_appends_without_selecting_existing_content(window, repo, qtbot, field):
    entry = repo.save(WordEntry("word", [Sense("n.", "first\nlast")],
                               [RelatedWord("child", [Sense("n.", "child\nlast")])]))
    window.reset_form(entry)
    window.search_box.setText("word")
    editor = {"search": window.search_box, "word": window.blocks[0].word,
              "translation": window.blocks[0].rows[0].translation,
              "related": window.blocks[1].word,
              "related_translation": window.blocks[1].rows[0].translation}[field]
    window.save_button.setFocus()
    editor.setFocus(Qt.FocusReason.TabFocusReason)
    assert caret_at_end(editor)
    before = editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()
    qtbot.keyClicks(editor, "X")
    assert (editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()) == before + "X"
    qtbot.keyClick(editor, Qt.Key.Key_Home)
    qtbot.keyClicks(editor, "Y")
    assert not caret_at_end(editor)  # Ongoing editing must not keep jumping to the end.
    qtbot.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert editor.hasSelectedText() if isinstance(editor, QLineEdit) else editor.textCursor().hasSelection()


@pytest.mark.parametrize("multiline", [False, True])
def test_initial_mouse_focus_goes_to_end_but_subsequent_click_can_place_caret(window, qtbot, multiline):
    editor = window.blocks[0].rows[0].translation if multiline else window.blocks[0].word
    if multiline:
        editor.setPlainText("first\nlast")
    else:
        editor.setText("first last")
    window.save_button.setFocus()
    target = editor.viewport() if multiline else editor
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    qtbot.waitUntil(lambda: caret_at_end(editor))
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert not caret_at_end(editor)


def test_settings_number_focus_goes_to_end(window, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.groups_per_day.setValue(123)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow)
    dialog.setFocus()
    dialog.groups_per_day.setFocus(Qt.FocusReason.TabFocusReason)
    editor = dialog.groups_per_day.findChild(QLineEdit)
    qtbot.waitUntil(lambda: caret_at_end(editor))
    dialog.reject()


@pytest.fixture(params=["pending", "all", "learned", "study17"])
def inline(window, repo, monkeypatch, request):
    entry = repo.save(WordEntry("word", [Sense("n.", "first\nlast")],
                               [RelatedWord("child", [Sense("n.", "child definition")])],
                               learned=request.param == "learned"))
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 2), False)))
    window.set_filter(request.param)
    table = window.current_table()
    table.revealRequested.emit(entry.id, True)
    yield table, entry
    table.delegate.cancel()


@pytest.mark.parametrize("column", [1, 3])
def test_double_clicked_text_editor_starts_at_end(inline, qtbot, column):
    table, entry = inline
    index = table.model().index(0, column)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    editor = table.delegate.editor
    assert isinstance(editor, (QLineEdit, QPlainTextEdit))
    qtbot.waitUntil(lambda: caret_at_end(editor))


@pytest.mark.parametrize("row", [0, 1])
def test_double_clicked_pos_opens_popup_and_supports_shortcuts(inline, repo, qtbot, row):
    table, entry = inline
    index = table.model().index(row, 2)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    editor = table.delegate.editor
    assert editor.currentData() == "n." and editor.view().isVisible()
    longest = max(editor.view().fontMetrics().horizontalAdvance(editor.itemText(i)) for i in range(editor.count()))
    assert not editor.view().wordWrap()
    assert editor.view().viewport().width() >= longest
    assert editor.view().width() <= longest + 24
    assert table.columnWidth(2) == table.pos_badge_width + 10
    assert editor.view().verticalScrollBar().maximum() == 0
    for i in range(editor.count()):
        assert editor.view().viewport().rect().contains(editor.view().visualItemRect(editor.view().item(i)))
    anchor_center = table.viewport().mapToGlobal(table.visualRect(index).center()).x()
    assert abs(editor.popup.geometry().center().x() - anchor_center) <= 1
    qtbot.keyClick(editor.view(), Qt.Key.Key_2)
    assert table.delegate.editor is None
    saved = repo.get(entry.id)
    assert (saved if row == 0 else saved.related[0]).senses[0].pos == "v."


def test_double_clicked_pos_escape_restores_opening_value(inline, repo, qtbot):
    table, entry = inline
    index = table.model().index(0, 2)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    editor = table.delegate.editor
    qtbot.keyClick(editor.view(), Qt.Key.Key_Down)
    qtbot.keyClick(editor.view(), Qt.Key.Key_Escape)
    assert table.delegate.editor is None
    assert repo.get(entry.id).senses[0].pos == "n."


def test_revealed_pos_does_not_show_tooltip(inline, monkeypatch):
    table, entry = inline
    tips, hidden = [], []
    monkeypatch.setattr("PySide6.QtWidgets.QToolTip.showText", lambda *args: tips.append(args))
    monkeypatch.setattr("PySide6.QtWidgets.QToolTip.hideText", lambda: hidden.append(True))
    for row in range(table.model().rowCount()):
        index = table.model().index(row, 2)
        assert index.data(Qt.ItemDataRole.ToolTipRole) is None
        point = table.visualRect(index).center()
        QApplication.sendEvent(table.viewport(), QHelpEvent(
            QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))
    assert not tips and hidden


def test_pos_choice_saves_and_closes_without_intermediate_editor(inline, repo, qtbot):
    table, entry = inline
    index = table.model().index(0, 2)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    editor = table.delegate.editor
    qtbot.keyClick(editor.view(), Qt.Key.Key_8)
    assert table.delegate.editor is None
    assert repo.get(entry.id).senses[0].pos == "interj."
    assert table.columnWidth(2) == table.pos_badge_width + 10


def test_pos_mouse_selection_commits_immediately(inline, repo, qtbot):
    table, entry = inline
    index = table.model().index(0, 2)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    editor = table.delegate.editor
    option = editor.view().visualItemRect(editor.view().item(3))
    qtbot.mouseClick(editor.view().viewport(), Qt.MouseButton.LeftButton, pos=option.center())
    assert table.delegate.editor is None
    assert repo.get(entry.id).senses[0].pos == "adv."


def test_pos_save_failure_keeps_popup_and_retries(inline, repo, qtbot, monkeypatch):
    table, entry = inline
    index = table.model().index(0, 2)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    editor = table.delegate.editor
    save = repo.save

    def fail(*args):
        raise OSError("locked")

    monkeypatch.setattr(repo, "save", fail)
    qtbot.keyClick(editor.view(), Qt.Key.Key_2)
    qtbot.wait(10)
    assert table.delegate.editor is editor and editor.view().isVisible()
    assert editor.currentData() == "v." and repo.get(entry.id).senses[0].pos == "n."
    monkeypatch.setattr(repo, "save", save)
    qtbot.keyClick(editor.view(), Qt.Key.Key_Return)
    assert table.delegate.editor is None and repo.get(entry.id).senses[0].pos == "v."


@pytest.mark.parametrize('background', ['toolbar', 'label', 'window_margin', 'history_header'])
def test_background_click_releases_button_focus_without_changing_its_checked_state(
        window, qtbot, monkeypatch, background):
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter('study17')
    page = window.study_page
    qtbot.mouseClick(page.completed_button, Qt.MouseButton.LeftButton)
    assert page.completed_button.hasFocus() and page.show_completed
    target, position = {
        'toolbar': (page, QPoint(page.width() // 2, page.completed_button.geometry().center().y())),
        'label': (page.period_label, page.period_label.rect().center()),
        'window_margin': (window.centralWidget(), QPoint(4, 4)),
        'history_header': (window.history_panel.title, window.history_panel.title.rect().center()),
    }[background]
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=position)
    assert not page.completed_button.hasFocus()
    assert QApplication.focusWidget() is window
    assert page.show_completed and page.completed_button.isChecked()
    assert page.completed_button.focusPolicy() & Qt.FocusPolicy.TabFocus
    page.completed_button.setFocus(Qt.FocusReason.TabFocusReason)
    assert page.completed_button.hasFocus()


def test_background_focus_release_keeps_tab_selection_and_keyboard_shortcuts(window, qtbot):
    qtbot.mouseClick(window.filter_buttons['all'], Qt.MouseButton.LeftButton)
    assert window.filter_name == 'all' and window.filter_buttons['all'].hasFocus()
    qtbot.mouseClick(window.centralWidget(), Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    assert not window.filter_buttons['all'].hasFocus() and window.filter_name == 'all'
    qtbot.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.AltModifier)
    assert window.filter_name == 'pending'


def test_interactive_clicks_and_input_drafts_keep_their_focus_behavior(window, qtbot):
    editor = window.blocks[0].word
    editor.setText('unfinished draft')
    window.save_button.setFocus()
    qtbot.mouseClick(editor, Qt.MouseButton.LeftButton)
    assert editor.hasFocus()
    qtbot.mouseClick(window.centralWidget(), Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    assert editor.hasFocus() and editor.text() == 'unfinished draft'
    window.global_hide_button.setFocus()
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(15, 20))
    assert not window.global_hide_button.hasFocus() and window.table.hasFocus()


def test_passive_form_scroll_background_releases_button_focus(window, qtbot):
    window.save_button.setFocus()
    qtbot.mouseClick(window.form_content, Qt.MouseButton.LeftButton,
                     pos=QPoint(window.form_content.width() - 2, window.form_content.height() - 2))
    assert not window.save_button.hasFocus()

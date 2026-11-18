import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from vocabulary.domain import DEFAULT_SHORTCUTS, RelatedWord, Sense, WordEntry
from vocabulary.shortcuts import SettingsDialog
from vocabulary.storage import Repository


def press_search(qtbot):
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)


@pytest.mark.parametrize("page", ["pending", "all", "learned", "study17"])
def test_search_shortcut_focuses_search_and_keeps_query(window, qtbot, page):
    window.search_box.setText("existing query")
    assert window.set_filter(page)
    window.current_table().setFocus()

    press_search(qtbot)

    assert window.filter_name == ("all" if page == "study17" else page)
    assert window.search_box.hasFocus()
    assert window.search_box.text() == "existing query"


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("open_dropdown", [False, True])
def test_search_shortcut_leaves_form_draft_intact(window, repo, qtbot, editing, open_dropdown):
    saved = repo.save(WordEntry("original", [Sense("n.", "词")]))
    if editing:
        window.reset_form(saved)
    block = window.blocks[0]
    block.word.setText("draft")
    block.rows[0].pos.set_pos("v.")
    draft = window.form_value()

    if open_dropdown:
        combo = block.rows[0].pos
        combo.setFocus()
        combo.showPopup()
        qtbot.waitUntil(lambda: QApplication.activePopupWidget() is not None)
    else:
        block.word.setFocus()

    press_search(qtbot)

    assert QApplication.activePopupWidget() is None
    assert window.search_box.hasFocus()
    assert window.form_value() == draft and window.is_dirty()
    assert window.edit_id == (saved.id if editing else None)
    assert repo.get(saved.id).text == "original"


def test_search_shortcut_submits_inline_edit_and_blocks_on_failure(window, repo, qtbot):
    entry = repo.save(WordEntry("original", [Sense("n.", "词")],
                                [RelatedWord("child", [Sense("n.", "关联词")])]))
    window.refresh()
    window.table.edit(window.model.index(0, 1))
    editor = window.table.delegate.editor
    editor.setText("")

    press_search(qtbot)

    assert window.table.delegate.editor is editor
    assert QApplication.focusWidget() is editor
    assert repo.get(entry.id).text == "original"

    editor.setText("updated")
    press_search(qtbot)

    assert window.table.delegate.editor is None
    assert repo.get(entry.id).text == "updated"
    assert window.search_box.hasFocus()


def test_search_shortcut_can_be_recorded_and_restored(window, repo, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    editor = dialog.editors["focus_search"]
    assert any(label.text() == "搜索单词" for label in dialog.findChildren(QLabel))
    assert editor.text() == "Ctrl+F"

    dialog.buttons["focus_search"].click()
    qtbot.keyClick(editor, Qt.Key.Key_F, Qt.KeyboardModifier.AltModifier)
    assert editor.text() == "Alt+F"
    reopened = Repository(repo.path)
    try:
        assert reopened.shortcuts()["focus_search"] == "Alt+F"
    finally:
        reopened.close()

    dialog.close()
    window.activateWindow()
    field = window.blocks[0].word
    field.setFocus()
    qtbot.waitUntil(field.hasFocus)
    press_search(qtbot)
    assert field.hasFocus()
    qtbot.keyClick(field, Qt.Key.Key_F, Qt.KeyboardModifier.AltModifier)
    assert window.search_box.hasFocus()

    dialog.restore_button.click()
    assert editor.text() == "Ctrl+F"
    assert window.shortcuts.values == DEFAULT_SHORTCUTS


def test_search_shortcut_is_added_without_replacing_existing_ctrl_f(repo):
    with repo.connection:
        repo.connection.execute("DELETE FROM settings WHERE key='focus_search'")
    repo.write_shortcuts({"save": "Ctrl+F"})

    reopened = Repository(repo.path)
    try:
        assert reopened.shortcuts()["save"] == "Ctrl+F"
        assert reopened.shortcuts()["focus_search"] == "Ctrl+Shift+F"
    finally:
        reopened.close()

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry


@pytest.mark.parametrize("key", [Qt.Key.Key_N, Qt.Key.Key_M, Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_I])
@pytest.mark.parametrize("target", ["search", "list", "toolbar", "outer_blank", "form_blank",
                                  "collapsed", "inline", "study"])
def test_form_shortcuts_outside_scope_preserve_draft_and_focus(window, repo, qtbot, monkeypatch, target, key):
    entry = repo.save(WordEntry("saved", [Sense("n.", "definition")]))
    window.reset_form(WordEntry("unfinished", [Sense("n.", "first"), Sense("v.", "second")],
                               [RelatedWord("child", [Sense("n.", "related")])]))
    window.refresh()
    window.blocks[0].word.setFocus()
    if target == "collapsed":
        window.set_form_collapsed(True)
    elif target == "inline":
        window.table.edit(window.model.index(0, 1))
        window.table.delegate.editor.setText("pending inline edit")
    elif target == "study":
        window.set_filter("study17")
    widget = {"search": window.search_box, "list": window.table,
              "toolbar": window.global_hide_button, "outer_blank": window,
              "form_blank": window.editor_panel, "collapsed": window.table,
              "inline": window.table.delegate.editor, "study": window.study_page.table}[target]
    widget.setFocus()
    before = window.form_value()
    collapsed = window.form_collapsed
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: pytest.fail("Unexpected removal"))
    monkeypatch.setattr("vocabulary.widgets.confirm_remove", lambda *args: pytest.fail("Unexpected removal"))
    assert window.add_related_button.text() == "新增关联词"

    qtbot.keyClick(widget, key, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)

    assert window.form_value() == before
    assert QApplication.focusWidget() is widget
    assert window.form_collapsed == collapsed
    assert repo.get(entry.id) == entry
    if target == "inline":
        assert window.table.delegate.editor is widget and widget.text() == "pending inline edit"


def test_remapped_form_shortcut_leaves_search_text_editing_available(window, qtbot):
    window.shortcuts.update(dict(window.shortcuts.values, add_related="Ctrl+Backspace"))
    window.search_box.setText("first second")
    window.search_box.setFocus()
    before = window.form_value()

    qtbot.keyClick(window.search_box, Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier)

    assert window.search_box.text() == "first "
    assert window.form_value() == before and window.search_box.hasFocus()


@pytest.mark.parametrize("key,words,senses,important", [
    (Qt.Key.Key_N, 3, 2, True), (Qt.Key.Key_M, 1, 2, True),
    (Qt.Key.Key_J, 2, 3, True), (Qt.Key.Key_K, 2, 1, True),
    (Qt.Key.Key_I, 2, 2, False),
])
def test_form_pos_popup_inherits_shortcut_scope(window, qtbot, monkeypatch, key, words, senses, important):
    window.reset_form(WordEntry("draft", [Sense("n.", "first"), Sense("v.", "second")],
                               [RelatedWord("child", [Sense("n.", "related")])]))
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: True)
    monkeypatch.setattr("vocabulary.widgets.confirm_remove", lambda *args: True)
    combo = window.blocks[0].rows[0].pos
    combo.setFocus()
    combo.showPopup()
    qtbot.waitUntil(lambda: QApplication.activePopupWidget() is not None)

    qtbot.keyClick(combo.view(), key, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)

    popup = QApplication.activePopupWidget()
    if popup is not None:
        popup.close()
    assert len(window.blocks) == words and len(window.blocks[0].rows) == senses
    assert window.star_button.isChecked() == important
    if key == Qt.Key.Key_N:
        assert [block.word.text() for block in window.blocks] == ["draft", "", "child"]

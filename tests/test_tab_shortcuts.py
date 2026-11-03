import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from vocabulary.domain import Sense, ValidationError, WordEntry


@pytest.fixture(params=[Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.ControlModifier],
                ids=["alt", "ctrl"])
def modifier(request):
    return request.param


def press_tab(window, qtbot, number, modifier):
    qtbot.keyClick(QApplication.focusWidget() or window, getattr(Qt.Key, f"Key_{number}"),
                   modifier)


@pytest.mark.parametrize("focus", ["form", "pos", "translation", "search", "list"])
def test_tabs_switch_from_any_main_window_input(window, qtbot, focus, modifier):
    {"form": window.blocks[0].word, "search": window.search_box,
     "pos": window.blocks[0].rows[0].pos, "translation": window.blocks[0].rows[0].translation,
     "list": window.table}[focus].setFocus()
    for number, page in [(2, "all"), (3, "learned"), (4, "study17"), (1, "pending")]:
        press_tab(window, qtbot, number, modifier)
        assert window.filter_name == page


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("number", [2, 3, 4])
def test_tab_switch_preserves_independent_form(window, repo, qtbot, editing, number, modifier, monkeypatch):
    saved = repo.save(WordEntry("original", [Sense("n.", "原词")]))
    if editing:
        window.reset_form(saved)
    block = window.blocks[0]
    block.word.setText("changed")
    block.rows[0].pos.set_pos("n.")
    block.rows[0].translation.setPlainText("修改")
    block.word.setFocus()
    draft = window.form_value()

    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("tab switch must not process draft"))
    press_tab(window, qtbot, number, modifier)
    assert window.filter_name == {2: "all", 3: "learned", 4: "study17"}[number]
    assert window.form_value() == draft and window.is_dirty()
    assert repo.get(saved.id).text == "original"
    window.set_filter("pending")
    assert window.form_value() == draft


def test_incomplete_draft_does_not_stop_tab_switch(window, repo, qtbot, modifier):
    window.blocks[0].word.setText("incomplete")
    press_tab(window, qtbot, 4, modifier)
    assert window.filter_name == "study17" and window.is_dirty()
    assert window.blocks[0].word.text() == "incomplete" and repo.list_entries() == []


def test_tab_switch_submits_inline_edit_and_blocks_invalid_input(window, repo, qtbot, modifier):
    saved = repo.save(WordEntry("original", [Sense("n.", "原词")]))
    window.reveal(saved.id, True)
    window.table.edit(window.model.index(0, 3))
    editor = window.table.delegate.editor
    editor.setPlainText("")
    press_tab(window, qtbot, 2, modifier)
    assert window.filter_name == "pending" and window.table.delegate.editor is editor
    editor.setPlainText("新释义")
    press_tab(window, qtbot, 2, modifier)
    assert window.filter_name == "all" and window.table.delegate.editor is None
    assert repo.get(saved.id).senses[0].translation == "新释义"


def test_mouse_tab_switch_preserves_draft(window, qtbot, monkeypatch):
    window.blocks[0].word.setText("draft")
    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("tab switch must not process draft"))
    qtbot.mouseClick(window.filter_buttons["all"], Qt.MouseButton.LeftButton)
    assert window.filter_name == "all" and window.blocks[0].word.text() == "draft"


def test_tab_shortcuts_suspend_and_do_not_repeat(window, qtbot, modifier):
    window.shortcuts.set_suspended(True)
    press_tab(window, qtbot, 2, modifier)
    assert window.filter_name == "pending"
    window.shortcuts.set_suspended(False)
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, modifier, "", True)
    QApplication.sendEvent(window.blocks[0].word, repeat)
    assert window.filter_name == "pending"
    for number in range(1, 5):
        with pytest.raises(ValidationError):
            prefix = "Alt" if modifier == Qt.KeyboardModifier.AltModifier else "Ctrl"
            window.shortcuts.update(dict(window.shortcuts.values, save=f"{prefix}+{number}"))


def test_aliases_dispatch_once_through_the_same_tab_switch(window, qtbot, monkeypatch):
    calls = []
    original = window.set_filter

    def switch(key):
        calls.append(key)
        original(key)

    monkeypatch.setattr(window, "set_filter", switch)
    for modifier in (Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.ControlModifier):
        for number in (2, 3, 4, 1):
            press_tab(window, qtbot, number, modifier)
    assert calls == ["all", "learned", "study17", "pending"] * 2


def test_form_title_tracks_editing_and_reset(window, repo):
    saved = repo.save(WordEntry("original", [Sense("n.", "原词")]))
    assert window.form_title.text() == "添加单词"
    window.reset_form(saved)
    assert window.form_title.text() == "编辑单词"
    window.set_form_collapsed(True)
    assert window.collapse_button.toolTip() == "展开编辑单词区域"
    QTimer.singleShot(10, lambda: QApplication.activeModalWidget().accept())
    window.cancel_form()
    assert window.form_title.text() == "添加单词"
    window.reset_form(saved)
    assert window._save_form()
    assert window.form_title.text() == "添加单词"

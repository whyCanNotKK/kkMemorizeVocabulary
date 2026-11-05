from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.shortcuts import SettingsDialog


def draft():
    return WordEntry("parent", [Sense("n.", "first"), Sense("v.", "second\nline"), Sense("adj.", "third")],
                     [RelatedWord("child", [Sense("n.", "child first"), Sense("v.", "child second"),
                                            Sense("adj.", "child third")])])


def test_shuffle_hides_during_search_and_returns_when_cleared(window):
    assert window.table.shuffle_button.isVisible()
    assert window.table.shuffle_button.toolTip() == "随机排列当前待记单词"
    window.search_box.setText("query")
    assert not window.table.shuffle_button.isVisible()
    window.search_box.setText("   ")
    assert window.table.shuffle_button.isVisible()


def test_related_actions_only_show_while_form_owns_focus(window, qtbot):
    window.reset_form(draft())
    window.search_box.setFocus()
    assert not window.add_related_button.isVisible()
    assert not window.remove_related_button.isVisible()
    for target in (window.blocks[0].word, window.blocks[0].rows[0].pos,
                   window.blocks[0].rows[0].translation):
        target.setFocus()
        assert window.add_related_button.isVisible()
        assert not window.remove_related_button.isVisible()
    window.blocks[1].word.setFocus()
    assert window.remove_related_button.isVisible()
    window.add_related_button.setFocus()
    assert window.remove_related_button.isVisible()
    window.save_button.setFocus()
    assert window.add_related_button.isVisible()
    assert not window.remove_related_button.isVisible()
    combo = window.blocks[1].rows[0].pos
    combo.setFocus()
    combo.showPopup()
    assert window.add_related_button.isVisible()
    assert window.remove_related_button.isVisible()
    combo.hidePopup()
    window.table.setFocus()
    assert not window.add_related_button.isVisible()
    assert not window.remove_related_button.isVisible()
    window.blocks[0].rows[0].translation.setFocus()
    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    dialog.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    dialog.show()
    dialog.activateWindow()
    dialog.setFocus()
    qtbot.waitUntil(dialog.hasFocus)
    assert not window.add_related_button.isVisible()
    assert not window.remove_related_button.isVisible()


def test_related_blocks_have_no_remove_button_and_footer_removes_focused(window, qtbot, monkeypatch):
    window.reset_form(draft())
    child = window.blocks[1]
    assert not any(button.text() == "移除" for button in child.findChildren(QPushButton))
    child.word.setFocus()
    assert window.remove_related_button.isVisible()
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: True)
    qtbot.mouseClick(window.remove_related_button, Qt.MouseButton.LeftButton)
    assert len(window.blocks) == 1
    assert not window.remove_related_button.isVisible()
    assert window.add_related_button.isVisible()


def test_definition_icons_insert_and_delete_their_own_row(window, qtbot, monkeypatch):
    block = window.blocks[0]
    row = block.rows[0]
    assert row.add_button.text() == row.remove_button.text() == ""
    assert not row.add_button.icon().isNull() and not row.remove_button.icon().isNull()
    assert row.remove_button.isVisible() and not row.remove_button.isEnabled()
    assert row.add_button.toolTip() == "新增释义 · Ctrl+J"
    assert row.remove_button.toolTip() == "删除释义 · Ctrl+K"
    qtbot.mouseClick(row.add_button, Qt.MouseButton.LeftButton)
    qtbot.wait(10)
    second = block.rows[1]
    second.translation.setPlainText("keep second")
    qtbot.mouseClick(row.add_button, Qt.MouseButton.LeftButton)
    qtbot.wait(10)
    inserted = block.rows[1]
    assert len(block.rows) == 3 and block.rows[2] is second
    assert inserted.pos.hasFocus()
    assert all(item.remove_button.isEnabled() for item in block.rows)
    qtbot.mouseClick(inserted.remove_button, Qt.MouseButton.LeftButton)
    assert block.rows == [row, second]
    monkeypatch.setattr("vocabulary.widgets.confirm_remove", lambda *args: False)
    qtbot.mouseClick(second.remove_button, Qt.MouseButton.LeftButton)
    assert len(block.rows) == 2
    monkeypatch.setattr("vocabulary.widgets.confirm_remove", lambda *args: True)
    qtbot.mouseClick(second.remove_button, Qt.MouseButton.LeftButton)
    assert block.rows == [row] and row.remove_button.isVisible() and not row.remove_button.isEnabled()


def test_definition_shortcuts_target_focused_row_and_update_tooltips(window, qtbot):
    block = window.blocks[0]
    block.add_sense()
    qtbot.wait(10)
    last = block.rows[-1]
    first = block.rows[0]
    first.translation.setFocus()
    qtbot.keyClick(first.translation, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    assert len(block.rows) == 3 and block.rows[-1] is last
    inserted = block.rows[1]
    qtbot.keyClick(inserted.pos, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert block.rows == [first, last]
    window.shortcuts.update(dict(window.shortcuts.values, add_sense="Alt+J", remove_sense="Alt+K"))
    assert all(row.add_button.toolTip() == "新增释义 · Alt+J" for row in block.rows)
    assert all(row.remove_button.toolTip() == "删除释义 · Alt+K" for row in block.rows)


@pytest.mark.parametrize("related", [False, True])
@pytest.mark.parametrize("field", ["pos", "translation", "add_button", "remove_button"])
def test_delete_shortcut_removes_focused_middle_definition(window, qtbot, monkeypatch, related, field):
    window.reset_form(draft())
    qtbot.wait(10)
    block = window.blocks[int(related)]
    rows = list(block.rows)
    monkeypatch.setattr("vocabulary.widgets.confirm_remove", lambda *args: True)
    target = getattr(rows[1], field)
    target.setFocus()
    qtbot.keyClick(target, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert block.rows == [rows[0], rows[2]]
    assert block.rows[-1].value().pos == "adj."


@pytest.mark.parametrize("related", [False, True])
def test_delete_shortcut_never_falls_back_from_word_name(window, qtbot, related):
    window.reset_form(draft())
    block = window.blocks[int(related)]
    rows = list(block.rows)
    for target in (block.word, window.search_box, window.table, window.save_button):
        target.setFocus()
        qtbot.keyClick(target, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        assert block.rows == rows


def test_delete_shortcut_keeps_focused_definition_when_cancelled(window, qtbot, monkeypatch):
    window.reset_form(draft())
    block = window.blocks[0]
    rows = list(block.rows)
    monkeypatch.setattr("vocabulary.widgets.confirm_remove", lambda *args: False)
    rows[0].translation.setFocus()
    qtbot.keyClick(rows[0].translation, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert block.rows == rows and rows[0].translation.hasFocus()


def test_settings_describe_definition_shortcuts(window, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    labels = {label.text() for label in dialog.findChildren(QLabel)}
    assert {"新增关联词", "删除关联词", "新增释义", "删除释义",
            "设置单词‘重点/非重点’", "保存单词"} <= labels


@pytest.mark.parametrize("related", [False, True])
@pytest.mark.parametrize("editing", [False, True])
def test_definition_drag_preserves_widgets_and_saves_order(window, repo, qtbot, related, editing):
    entry = repo.save(draft()) if editing else draft()
    window.resize(1150, 1400)
    window.reset_form(entry)
    qtbot.wait(40)
    block = window.blocks[int(related)]
    other = window.blocks[int(not related)]
    other_value = deepcopy(other.value())
    rows = list(block.rows)
    viewport = window.form_scroll.viewport()
    drag = rows[0].drag_handle
    destination = rows[-1].mapTo(viewport, QPoint(10, rows[-1].height() - 2))
    qtbot.mousePress(drag, Qt.MouseButton.LeftButton, pos=drag.rect().center())
    qtbot.mouseMove(drag, drag.mapFromGlobal(viewport.mapToGlobal(destination)), delay=20)
    assert window.form_drag.active and window.shortcuts.suspended
    assert block.rows == rows and not window.form_drag.preview.grab().isNull()
    # Rounded input corners and tool gaps must retain the dark panel background.
    image = window.form_drag.images[rows[0]].toImage()
    corner = image.pixelColor(0, 0)
    assert corner.alpha() == 0 or max(corner.red(), corner.green(), corner.blue()) < 100
    qtbot.mouseRelease(drag, Qt.MouseButton.LeftButton,
                       pos=drag.mapFromGlobal(viewport.mapToGlobal(destination)))
    assert block.rows == [rows[1], rows[2], rows[0]]
    assert other.value() == other_value and not window.shortcuts.suspended
    assert rows[0].translation.toPlainText() == ("child first" if related else "first")
    assert block.word.nextInFocusChain() is block.phonetic
    assert block.phonetic.nextInFocusChain() is rows[1].pos
    assert window._save_form()
    saved = repo.get(entry.id) if editing else repo.list_entries()[0]
    senses = saved.related[0].senses if related else saved.senses
    assert [sense.pos for sense in senses] == ["v.", "adj.", "n."]


@pytest.mark.parametrize("related", [False, True])
@pytest.mark.parametrize("cancel", ["escape", "outside", "other-word", "threshold", "same"])
def test_definition_drag_cancel_keeps_original_inputs(window, qtbot, related, cancel):
    window.resize(1150, 1400)
    window.reset_form(draft())
    qtbot.wait(40)
    block = window.blocks[int(related)]
    rows = list(block.rows)
    before = window.form_value()
    drag = rows[0].drag_handle
    viewport = window.form_scroll.viewport()
    origin = drag.rect().center()
    destination = rows[-1].mapTo(viewport, QPoint(10, rows[-1].height() - 2))
    if cancel in ("same", "threshold"):
        destination = drag.mapTo(viewport, origin + QPoint(15 if cancel == "same" else 1, 0))
    qtbot.mousePress(drag, Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(drag, drag.mapFromGlobal(viewport.mapToGlobal(destination)), delay=20)
    if cancel == "escape":
        qtbot.keyClick(window, Qt.Key.Key_Escape)
    elif cancel == "outside":
        destination = QPoint(-5, -5)
    elif cancel == "other-word":
        destination = window.blocks[int(not related)].rows[0].mapTo(viewport, QPoint(10, 10))
    qtbot.mouseRelease(drag, Qt.MouseButton.LeftButton,
                       pos=drag.mapFromGlobal(viewport.mapToGlobal(destination)))
    assert block.rows == rows and window.form_value() == before
    assert not window.form_drag.active and not window.shortcuts.suspended

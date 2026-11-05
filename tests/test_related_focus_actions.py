import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.shortcuts import SettingsDialog


def entry():
    return WordEntry("main", [Sense("n.", "main definition")],
                     [RelatedWord(name, [Sense("n.", name), Sense("v.", "extra")])
                      for name in ("alpha", "beta", "gamma")])


def field(block, name):
    return getattr(block, name) if name in ("word", "phonetic") else getattr(block.rows[-1], name)


@pytest.mark.parametrize("source", [0, 1, 2])
@pytest.mark.parametrize("input_name", ["word", "phonetic", "pos", "translation"])
@pytest.mark.parametrize("trigger", ["button", "shortcut"])
def test_add_inserts_after_focused_word_without_rebuilding_existing_inputs(
        window, qtbot, source, input_name, trigger):
    window.reset_form(entry())
    qtbot.wait(10)
    before = list(window.blocks)
    source_field = field(before[source], input_name)
    source_field.setFocus()
    if trigger == "button":
        qtbot.mouseClick(window.add_related_button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(source_field, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    created = window.blocks[source + 1]
    assert created not in before and created.word.hasFocus()
    assert window.blocks[:source + 1] == before[:source + 1]
    assert window.blocks[source + 2:] == before[source + 1:]
    last = before[source].input_order()[-1]
    last.setFocus()
    qtbot.keyClick(last, Qt.Key.Key_Tab)
    assert created.word.hasFocus()


@pytest.mark.parametrize("source", [1, 2, 3])
@pytest.mark.parametrize("input_name", ["word", "phonetic", "pos", "translation"])
@pytest.mark.parametrize("trigger", ["button", "shortcut"])
def test_remove_targets_focused_related_word(window, qtbot, monkeypatch, source, input_name, trigger):
    window.reset_form(entry())
    qtbot.wait(10)
    before = list(window.blocks)
    target = field(before[source], input_name)
    target.setFocus()
    confirmations = []
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: confirmations.append(args[-1]) or True)
    if trigger == "button":
        assert window.remove_related_button.isEnabled()
        qtbot.mouseClick(window.remove_related_button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(target, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    assert window.blocks == before[:source] + before[source + 1:]
    assert len(confirmations) == 1
    assert window.blocks[min(source, len(window.blocks) - 1)].word.hasFocus()


def test_remove_outside_form_and_cancel_preserve_related_words(window, qtbot, monkeypatch):
    window.reset_form(entry())
    before = list(window.blocks)
    for target in (window.search_box, window.table):
        target.setFocus()
        qtbot.keyClick(target, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
        assert window.blocks == before
    window.blocks[1].word.setFocus()
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: False)
    qtbot.keyClick(window.blocks[1].word, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    assert window.blocks == before and window.blocks[1].word.hasFocus()


def test_related_focus_order_persists_and_remapped_hints_match(window, repo, qtbot, monkeypatch):
    saved = repo.save(entry())
    window.reset_form(saved)
    window.blocks[1].word.setFocus()
    window.shortcuts.update(dict(window.shortcuts.values, add_related="Alt+N", remove_related="Alt+M"))
    assert window.add_related_button.toolTip() == "新增关联词 · Alt+N"
    assert window.remove_related_button.toolTip() == "删除关联词 · Alt+M"
    assert window.remove_related_button.text() == "删除关联词  Alt+M"
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    labels = {label.text() for label in dialog.findChildren(QLabel)}
    assert {"新增关联词", "删除关联词"} <= labels
    window.blocks[1].word.setFocus()
    qtbot.keyClick(window.blocks[1].word, Qt.Key.Key_N, Qt.KeyboardModifier.AltModifier)
    qtbot.wait(10)
    created = window.blocks[2]
    created.word.setText("inserted")
    created.rows[0].pos.set_pos("n.")
    created.rows[0].translation.setPlainText("inserted definition")
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: True)
    target = window.blocks[3].rows[1].translation
    target.setFocus()
    qtbot.keyClick(target, Qt.Key.Key_M, Qt.KeyboardModifier.AltModifier)
    assert window._save_form()
    assert [child.text for child in repo.get(saved.id).related] == ["alpha", "inserted", "gamma"]

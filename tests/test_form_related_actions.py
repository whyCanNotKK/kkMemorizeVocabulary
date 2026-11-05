import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry


def populate(window, qtbot):
    window.reset_form(WordEntry('main', [Sense('n.', '主词')], [
        RelatedWord(text, [Sense('n.', text)]) for text in ('alpha', 'beta', 'gamma')]))
    window.blocks[1].word.setFocus()
    qtbot.wait(10)


def focus_target(window, qtbot, name):
    if name == 'form_blank':
        qtbot.mouseClick(window.form_title, Qt.MouseButton.LeftButton)
    elif name == 'outer_blank':
        qtbot.mouseClick(window.centralWidget(), Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    else:
        {'main': window.blocks[0].word, 'search': window.search_box,
         'list': window.table, 'save': window.save_button}[name].setFocus()


@pytest.mark.parametrize('target', ['search', 'list', 'outer_blank', 'form_blank', 'save', 'main'])
@pytest.mark.parametrize('trigger', ['button', 'shortcut'])
def test_add_is_always_visible_and_appends_without_a_word_target(window, qtbot, target, trigger):
    populate(window, qtbot)
    before = list(window.blocks)
    focus_target(window, qtbot, target)
    assert window.add_related_button.isVisible()
    if target in ('search', 'list', 'outer_blank', 'form_blank'):
        assert window.add_related_button.text() == window.add_related_button.toolTip() == '新增关联词'
    else:
        assert window.add_related_button.text() == '新增关联词  Ctrl+N'
    if trigger == 'button':
        qtbot.mouseClick(window.add_related_button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(QApplication.focusWidget() or window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    index = 1 if target == 'main' else len(before)
    created = window.blocks[index]
    assert created not in before and created.word.hasFocus()
    assert window.blocks[:index] == before[:index]
    assert window.blocks[index + 1:] == before[index:]


@pytest.mark.parametrize('target', ['main', 'form_blank', 'save'])
@pytest.mark.parametrize('trigger', ['button', 'shortcut'])
def test_delete_last_uses_the_existing_delete_function(window, qtbot, monkeypatch, target, trigger):
    populate(window, qtbot)
    before = list(window.blocks)
    focus_target(window, qtbot, target)
    assert window.remove_related_button.isVisible()
    assert window.remove_related_button.text() == ('删除末尾关联词' if target == 'form_blank'
                                                 else '删除末尾关联词  Ctrl+M')
    removed = []
    remove = window.remove_related
    monkeypatch.setattr(window, 'remove_related', lambda block: (removed.append(block), remove(block)))
    monkeypatch.setattr('vocabulary.window.confirm_remove', lambda *args: True)
    if trigger == 'button':
        qtbot.mouseClick(window.remove_related_button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    assert removed == [before[-1]] and window.blocks == before[:-1]
    assert window.blocks[-1].word.hasFocus()


@pytest.mark.parametrize('target', ['search', 'list', 'outer_blank'])
def test_outside_form_offers_delete_last_without_dispatching_shortcut(window, qtbot, target):
    populate(window, qtbot)
    before = list(window.blocks)
    focus_target(window, qtbot, target)
    assert window.add_related_button.isVisible() and window.remove_related_button.isVisible()
    assert window.remove_related_button.text() == window.remove_related_button.toolTip() == '删除末尾关联词'
    qtbot.keyClick(QApplication.focusWidget() or window, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    assert window.blocks == before


def test_blank_click_clears_input_target_and_hints(window, qtbot):
    populate(window, qtbot)
    focus_target(window, qtbot, 'form_blank')
    assert window.focused_block() is None
    assert window.add_related_button.text() == '新增关联词'
    assert window.remove_related_button.text() == '删除末尾关联词'
    focus_target(window, qtbot, 'outer_blank')
    assert window.add_related_button.text() == '新增关联词'
    assert window.remove_related_button.isVisible()
    assert window.remove_related_button.text() == '删除末尾关联词'


@pytest.mark.parametrize('target', ['search', 'list', 'outer_blank', 'form_blank'])
def test_plain_delete_button_removes_current_last_and_hides_only_when_empty(window, qtbot, monkeypatch, target):
    populate(window, qtbot)
    window.move_form_related(0, 2)
    before = list(window.blocks)
    assert before[-1].word.text() == 'alpha'
    focus_target(window, qtbot, target)
    monkeypatch.setattr('vocabulary.window.confirm_remove', lambda *args: False)
    qtbot.mouseClick(window.remove_related_button, Qt.MouseButton.LeftButton)
    assert window.blocks == before
    removed = []
    remove = window.remove_related
    monkeypatch.setattr(window, 'remove_related', lambda block: (removed.append(block), remove(block)))
    monkeypatch.setattr('vocabulary.window.confirm_remove', lambda *args: True)
    for last in reversed(before[1:]):
        focus_target(window, qtbot, target)
        assert window.remove_related_button.isVisible() and window.remove_related_button.isEnabled()
        assert window.remove_related_button.text() == window.remove_related_button.toolTip() == '删除末尾关联词'
        qtbot.mouseClick(window.remove_related_button, Qt.MouseButton.LeftButton)
        assert removed[-1] is last and last not in window.blocks
    assert window.blocks == before[:1]
    assert not window.remove_related_button.isVisible()
    focus_target(window, qtbot, target)
    assert not window.remove_related_button.isVisible()


def test_delete_last_uses_current_draft_order_and_confirmation(window, qtbot, monkeypatch):
    populate(window, qtbot)
    window.move_form_related(0, 2)
    before = list(window.blocks)
    assert before[-1].word.text() == 'alpha'
    window.blocks[0].word.setFocus()
    with monkeypatch.context() as patch:
        patch.setattr('vocabulary.window.confirm_remove', lambda *args: False)
        window.remove_focused_related()
    assert window.blocks == before and before[-1].word.hasFocus()
    focus_target(window, qtbot, 'form_blank')
    monkeypatch.setattr('vocabulary.window.confirm_remove', lambda *args: True)
    window.remove_focused_related()
    assert window.blocks == before[:-1]


def test_shortcut_labels_follow_context_and_remapping(window, qtbot):
    populate(window, qtbot)
    window.search_box.setFocus()
    window.shortcuts.update(dict(window.shortcuts.values, add_related='Alt+N', remove_related='Alt+M'))
    assert window.add_related_button.text() == window.add_related_button.toolTip() == '新增关联词'
    assert window.remove_related_button.text() == window.remove_related_button.toolTip() == '删除末尾关联词'
    focus_target(window, qtbot, 'form_blank')
    assert window.add_related_button.text() == '新增关联词'
    assert window.remove_related_button.text() == '删除末尾关联词'
    window.blocks[0].word.setFocus()
    assert window.add_related_button.text() == '新增关联词  Alt+N'
    assert window.remove_related_button.text() == '删除末尾关联词  Alt+M'
    window.blocks[1].word.setFocus()
    assert window.remove_related_button.text() == '删除关联词  Alt+M'


def test_blank_target_is_not_restored_after_collapsing_form(window, qtbot):
    populate(window, qtbot)
    before = list(window.blocks)
    focus_target(window, qtbot, 'form_blank')
    window.set_form_collapsed(True)
    window.set_form_collapsed(False)
    qtbot.wait(10)
    assert window.focused_block() is None
    qtbot.mouseClick(window.add_related_button, Qt.MouseButton.LeftButton)
    qtbot.wait(10)
    assert window.blocks[:-1] == before

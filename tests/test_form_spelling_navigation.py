import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry


def populate(window, count=3):
    window.reset_form(WordEntry('main', [Sense('n.', 'meaning')],
                                [RelatedWord(f'child{i}', [Sense('v.', 'meaning')]) for i in range(count)]))
    window.blocks[0].word.setFocus()


@pytest.mark.parametrize('editing', [False, True])
@pytest.mark.parametrize('key', [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_enter_moves_between_spellings_without_saving(window, repo, qtbot, editing, key):
    populate(window)
    if editing:
        window.reset_form(repo.save(window.form_value()))
    window.blocks[0].word.setText('unsaved')
    window.blocks[0].word.setFocus()
    before = window.form_value()
    saved = repo.list_entries()
    for block in window.blocks[1:]:
        qtbot.keyClick(QApplication.focusWidget(), key)
        assert block.word.hasFocus()
        assert block.word.cursorPosition() == len(block.word.text())
        assert not block.word.hasSelectedText()
    # Last and first spelling fields do not wrap.
    qtbot.keyClick(QApplication.focusWidget(), key)
    assert window.blocks[-1].word.hasFocus()
    for block in reversed(window.blocks[:-1]):
        qtbot.keyClick(QApplication.focusWidget(), key, Qt.KeyboardModifier.ShiftModifier)
        assert block.word.hasFocus()
    qtbot.keyClick(QApplication.focusWidget(), key, Qt.KeyboardModifier.ShiftModifier)
    assert window.blocks[0].word.hasFocus()
    assert window.form_value() == before and repo.list_entries() == saved


def test_spelling_navigation_uses_current_draft_order_and_allows_empty_input(window, qtbot):
    populate(window)
    window.move_form_related(0, 2)
    window.blocks[0].word.clear()
    window.blocks[0].word.setFocus()
    qtbot.keyClick(window.blocks[0].word, Qt.Key.Key_Return)
    assert window.blocks[1].word.hasFocus() and window.blocks[1].word.text() == 'child1'
    # New blocks and removal are reflected immediately.
    added = window.add_block(related=True, position=2)
    qtbot.keyClick(window.blocks[1].word, Qt.Key.Key_Return)
    assert added.word.hasFocus()
    window.remove_related(added)
    window.blocks[1].word.setFocus()
    qtbot.keyClick(window.blocks[1].word, Qt.Key.Key_Return)
    assert window.blocks[2].word.hasFocus() and window.blocks[2].word.text() == 'child2'


def test_spelling_navigation_does_not_capture_other_fields_or_modified_shortcuts(window, qtbot, monkeypatch):
    populate(window)
    word = window.blocks[0].word
    qtbot.keyClick(word, Qt.Key.Key_Return, Qt.KeyboardModifier.AltModifier)
    assert word.hasFocus()
    QApplication.sendEvent(word, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                          Qt.KeyboardModifier.NoModifier, '', True))
    assert word.hasFocus()
    calls = []
    monkeypatch.setattr(window, '_save_form', lambda: calls.append(True))
    qtbot.keyClick(word, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert calls == [True] and word.hasFocus()
    phonetic = window.blocks[0].phonetic
    phonetic.setFocus()
    qtbot.keyClick(phonetic, Qt.Key.Key_Return)
    assert phonetic.hasFocus()
    translation = window.blocks[0].rows[0].translation
    translation.setFocus()
    qtbot.keyClick(translation, Qt.Key.Key_Return)
    assert translation.hasFocus() and translation.toPlainText().endswith('\n')


def test_spelling_navigation_scrolls_to_target_and_ignores_busy_input(window, qtbot):
    populate(window, count=15)
    qtbot.wait(20)
    word = window.blocks[0].word
    with window.busy_overlay.display('testing'):
        qtbot.keyClick(word, Qt.Key.Key_Return)
    assert word.hasFocus()
    for block in window.blocks[1:]:
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)
        assert block.word.hasFocus()
    qtbot.wait(20)
    target = window.blocks[-1].word
    viewport = window.form_scroll.viewport()
    point = target.mapTo(viewport, target.rect().center())
    assert viewport.rect().contains(point)
    assert window.form_scroll.verticalScrollBar().value() > 0


def test_only_main_spelling_has_no_enter_destination(window, qtbot):
    word = window.blocks[0].word
    word.setFocus()
    for modifiers in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
        qtbot.keyClick(word, Qt.Key.Key_Enter, modifiers | Qt.KeyboardModifier.KeypadModifier)
        assert word.hasFocus() and len(window.blocks) == 1

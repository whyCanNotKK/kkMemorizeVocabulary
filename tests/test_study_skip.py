from datetime import date

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture
def skip_list(window, repo, monkeypatch):
    monkeypatch.setattr(StudyContext, 'now', classmethod(
        lambda cls: StudyContext(date(2026, 11, 16), False)))
    words = [repo.save(WordEntry(name, [Sense('n.', '释义'), Sense('v.', '第二义')],
                                [RelatedWord('child', [Sense('n.', '关联词')])]))
             for name in ('alpha', 'beta', 'gamma', 'done')]
    window.set_filter('study17')
    page = window.study_page
    page.set_global_hidden(False)
    page.complete(words[-1].id, 'study')
    page.toggle_completed(True)
    page.timer.pause()
    page.table.select_entry(words[0].id)
    page.table.setFocus()
    return page, words


def trigger_skip(qtbot, table, entry, trigger):
    if trigger == 'mouse':
        index = table._entry_index(entry.id, column=table.action_column)
        row = index.data(Qt.ItemDataRole.UserRole)
        point = dict(table.action_rects(table.visualRect(index), row))['study'].center()
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=point)
    else:
        key = Qt.Key.Key_Return if trigger == 'return' else Qt.Key.Key_Enter
        modifier = (Qt.KeyboardModifier.NoModifier if trigger == 'return'
                    else Qt.KeyboardModifier.KeypadModifier)
        qtbot.keyClick(table, key, modifier)


@pytest.mark.parametrize('trigger', ['mouse', 'return', 'keypad'])
def test_skip_cycles_only_pending_words_without_changing_progress(skip_list, repo, qtbot, trigger):
    page, words = skip_list
    table = page.table
    before = repo.study_backup()
    related = table._entry_index(words[0].id, owner=0, column=3)
    table.setCurrentIndex(related)
    qtbot.keyClicks(table, 'al')
    assert table.study_input.position == 2
    for source, target in ((words[0], words[1]), (words[1], words[2]), (words[2], words[0])):
        trigger_skip(qtbot, table, source, trigger)
        assert table.currentIndex() == table._entry_index(target.id)
        assert table.hasFocus()
        assert table.study_input.position == 0
        assert not page.timer.running
        assert repo.study_backup() == before
        QApplication.sendEvent(table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                               Qt.KeyboardModifier.NoModifier, '\r', True))
        assert table.currentIndex() == table._entry_index(target.id)


@pytest.mark.parametrize('trigger', ['mouse', 'return', 'keypad'])
def test_only_pending_word_keeps_focus_and_spelling_progress(skip_list, repo, qtbot, trigger):
    page, words = skip_list
    for word in words[1:3]:
        page.complete(word.id, 'study')
    table = page.table
    table.select_entry(words[0].id)
    table.setFocus()
    qtbot.keyClicks(table, 'al')
    current = table.currentIndex()
    before = repo.study_backup()
    trigger_skip(qtbot, table, words[0], trigger)
    assert table.currentIndex() == current and table.hasFocus()
    assert table.study_input.position == 2
    assert repo.study_backup() == before


@pytest.mark.parametrize('trigger', ['mouse', 'return', 'keypad'])
def test_hidden_skip_reveals_source_before_moving_and_keeps_target_hidden(skip_list, repo, qtbot, trigger):
    page, words = skip_list
    table = page.table
    page.set_global_hidden(True)
    before = repo.study_backup().records
    counts = page.new_count.accessibleName(), page.review_count.accessibleName()
    revealed_before_leaving = []
    table.revealRequested.connect(lambda word_id, expanded: revealed_before_leaving.append(
        (word_id, expanded, word_id in page.revealed,
         table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id)))
    for source, target in ((words[0], words[1]), (words[1], words[2]), (words[2], words[0])):
        trigger_skip(qtbot, table, source, trigger)
        assert revealed_before_leaving[-1] == (source.id, True, True, source.id)
        assert table.currentIndex() == table._entry_index(target.id)
        assert all(not row.hidden for row in page.model.rows if row.entry.id == source.id)
        if target is not words[0]:
            assert table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
        assert table.hasFocus()
        assert repo.study_backup().records == before
        assert (page.new_count.accessibleName(), page.review_count.accessibleName()) == counts
    assert page.timer.running  # Single-word reveal uses the existing timer entry point.


@pytest.mark.parametrize('trigger', ['mouse', 'return', 'keypad'])
def test_hidden_single_remaining_word_is_revealed_and_keeps_focus(skip_list, repo, qtbot, trigger):
    page, words = skip_list
    for word in words[1:3]:
        page.complete(word.id, 'study')
    page.set_global_hidden(True)
    table = page.table
    table.select_entry(words[0].id)
    table.setFocus()
    before = repo.study_backup().records
    trigger_skip(qtbot, table, words[0], trigger)
    assert table.currentIndex() == table._entry_index(words[0].id)
    assert not table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
    assert table.hasFocus() and repo.study_backup().records == before


def test_failed_reveal_does_not_skip(skip_list, monkeypatch, qtbot):
    page, words = skip_list
    page.set_global_hidden(True)
    monkeypatch.setattr(page, 'start_timer', lambda: False)
    table = page.table
    for trigger in ('mouse', 'return', 'keypad'):
        trigger_skip(qtbot, table, words[0], trigger)
        assert table.currentIndex() == table._entry_index(words[0].id)
        assert table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden


@pytest.mark.parametrize('hidden', [False, True])
def test_right_skip_uses_clicked_word_and_completed_enter_is_inert(skip_list, qtbot, hidden):
    page, words = skip_list
    table = page.table
    if hidden:
        page.reveal(words[2].id, False)
    trigger_skip(qtbot, table, words[2], 'mouse')
    assert table.currentIndex() == table._entry_index(words[0].id)
    assert words[2].id in page.revealed
    table.select_entry(words[-1].id)
    current = table.currentIndex()
    trigger_skip(qtbot, table, words[-1], 'return')
    assert table.currentIndex() == current


def test_skip_does_not_leave_invalid_inline_editor(skip_list, qtbot):
    page, words = skip_list
    table = page.table
    table.edit(table._entry_index(words[0].id))
    editor = table.delegate.editor
    editor.setText('')  # Main spelling is required while related words exist.
    trigger_skip(qtbot, table, words[1], 'mouse')
    assert table.delegate.editor is editor and editor.text() == ''
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == words[0].id
    table.delegate.cancel()

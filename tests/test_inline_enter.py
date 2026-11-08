from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture(params=['pending', 'study17'])
def inline_table(request, window, repo, monkeypatch, qtbot):
    entry = repo.save(WordEntry('word', [Sense('n.', '原释义')]))
    if request.param == 'study17':
        context = StudyContext(date(2026, 11, 2), False)
        monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: context))
        window.set_filter('study17')
        window.study_page.reveal(entry.id, True)
    else:
        window.refresh()
        window.reveal(entry.id, True)
    table = window.current_table()
    yield table, entry
    table.delegate.cancel()


@pytest.mark.parametrize('key,modifiers', [
    (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier),
])
@pytest.mark.parametrize('column,value', [(1, 'renamed'), (2, 'v.'), (3, 'new definition')])
def test_enter_saves_cell_without_newline_or_completing_study(
        inline_table, repo, qtbot, column, value, key, modifiers):
    table, entry = inline_table
    before = repo.study_backup()
    table.edit(table.model().index(0, column))
    editor = table.delegate.editor
    assert editor is not None
    if column == 1:
        editor.setText(value)
    elif column == 2:
        editor.set_pos(value)
    else:
        editor.setPlainText(value)
    qtbot.keyClick(editor, key, modifiers)
    assert table.delegate.editor is None
    qtbot.wait(10)
    current = repo.get(entry.id)
    assert (current.text if column == 1 else current.senses[0].pos if column == 2
            else current.senses[0].translation) == value
    assert table.hasFocus()
    assert list(repo.study_backup().records.values()) == list(before.records.values())
    assert table.model().rowCount() == 1


@pytest.mark.parametrize('key', [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_shift_enter_adds_newline_then_enter_saves(inline_table, repo, qtbot, key):
    table, entry = inline_table
    table.edit(table.model().index(0, 3))
    editor = table.delegate.editor
    editor.setPlainText('first')
    editor.moveCursor(editor.textCursor().MoveOperation.End)
    qtbot.keyClick(editor, key, Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClicks(editor, 'second')
    assert table.delegate.editor is editor
    assert editor.toPlainText() == 'first\nsecond'
    assert repo.get(entry.id).senses[0].translation == '原释义'
    qtbot.keyClick(editor, key)
    assert table.delegate.editor is None
    assert repo.get(entry.id).senses[0].translation == 'first\nsecond'


def test_blank_main_removes_entry_and_detaches_open_form(inline_table, window, repo, qtbot):
    table, entry = inline_table
    studying = window.filter_name == 'study17'
    window.reset_form(entry)
    if not studying:
        window.blocks[0].rows[0].translation.setPlainText('unsaved draft')
    table.edit(table.model().index(0, 1))
    editor = table.delegate.editor
    editor.setText(' \t\n ')
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert table.delegate.editor is None
    assert table.model().rowCount() == 0
    assert repo.list_entries() == []
    assert window.edit_id is None
    if studying:
        assert repo.study_participant_count() == 0
        assert window.study_page.timer.history.new_total == 0
        assert not window.study_page.timer.running
        assert not window.is_dirty()
    else:
        assert window.is_dirty()
        assert window.blocks[0].rows[0].translation.toPlainText() == 'unsaved draft'
        assert window.save_current()
        assert repo.list_entries()[0].senses == [Sense('n.', 'unsaved draft')]


@pytest.mark.parametrize('failure', ['empty', 'duplicate', 'database'])
def test_enter_save_failure_preserves_input_and_editor(inline_table, repo, qtbot, monkeypatch, failure):
    table, entry = inline_table
    if failure == 'empty':
        entry.related.append(RelatedWord('child', [Sense('n.', 'child')]))
        repo.save(entry)
    repo.save(WordEntry('existing', [Sense('n.', '已有')]))
    table.edit(table.model().index(0, 1))
    editor = table.delegate.editor
    value = '' if failure == 'empty' else 'existing' if failure == 'duplicate' else 'new name'
    editor.setText(value)
    if failure == 'database':
        def fail(*args):
            raise sqlite3.OperationalError('disk full')
        monkeypatch.setattr(repo, 'save', fail)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert table.delegate.editor is editor
    assert editor.text() == value
    assert repo.get(entry.id).text == 'word'

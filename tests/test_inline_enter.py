from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import Sense, WordEntry
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


@pytest.mark.parametrize('failure', ['empty', 'duplicate', 'database'])
def test_enter_save_failure_preserves_input_and_editor(inline_table, repo, qtbot, monkeypatch, failure):
    table, entry = inline_table
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

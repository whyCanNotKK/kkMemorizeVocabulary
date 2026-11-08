from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture(params=['pending', 'all', 'learned', 'study17'])
def edit_list(request, window, repo, qtbot, monkeypatch):
    entries = [repo.save(WordEntry(name, [Sense('n.', 'first'), Sense('v.', 'second')],
               [RelatedWord(name + '-child', [Sense('n.', 'child first'), Sense('v.', 'child second')])],
               learned=request.param == 'learned')) for name in ['alpha', 'beta']]
    monkeypatch.setattr(StudyContext, 'now', classmethod(
        lambda cls: StudyContext(date(2026, 11, 2), False)))
    window.set_filter(request.param)
    table = window.current_table()
    table.revealRequested.emit(entries[0].id, True)
    yield table, entries
    table.delegate.cancel()


def field(table):
    index = QModelIndex(table.delegate.edit_index)
    row = index.data(Qt.ItemDataRole.UserRole)
    return row.entry.id, row.owner, row.sense, index.column()


@pytest.mark.parametrize('owner,sense,column', [(-1, 0, 1), (-1, 1, 2), (0, 1, 3)])
def test_f2_edits_current_rows_translation(edit_list, qtbot, owner, sense, column):
    table, entries = edit_list
    number = next(i for i, row in enumerate(table.model().rows)
                  if (row.entry.id, row.owner, row.sense) == (entries[0].id, owner, sense))
    table.setCurrentIndex(table.model().index(number, column))
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == (entries[0].id, owner, sense, 3)


def test_f2_reveals_hidden_entry_and_does_not_complete_it(edit_list, repo, qtbot):
    table, entries = edit_list
    table.revealRequested.emit(entries[0].id, False)
    table.setCurrentIndex(table.model().index(0, 1))
    table.setFocus()
    before = repo.study_backup()
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == (entries[0].id, -1, 0, 3)
    assert not table.model().rows[0].hidden
    assert repo.study_backup() == before


def test_tab_traverses_all_fields_in_both_directions_and_stops_at_edges(edit_list, repo, qtbot, monkeypatch):
    table, entries = edit_list
    before = repo.study_backup()
    monkeypatch.setattr(repo, 'prepare_study', lambda *args: pytest.fail('editing must not draw words'))
    expected = [(entry.id, owner, sense, column) for entry in entries
                for owner in (-1, 0)
                for sense, column in [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3)]]
    table.edit(table.model().index(0, 1))
    for target in expected:
        assert table.delegate.editor is not None
        assert field(table) == target
        if target[3] == 1:
            qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Tab)
            assert table.delegate.editor.phonetic.hasFocus()
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        qtbot.wait(5)  # pending refresh must not destroy the next editor
    assert table.delegate.editor is None
    last = expected[-1]
    table.edit_field(last)
    for target in reversed(expected):
        assert field(table) == target
        if target[3] == 1:
            qtbot.keyClick(table.delegate.editor.phonetic, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
            assert table.delegate.editor.hasFocus()
        qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        qtbot.wait(5)
    assert table.delegate.editor is None
    assert table.hasFocus()
    assert repo.study_backup() == before


def test_tab_saves_and_backtab_reads_latest_value(edit_list, repo, qtbot):
    table, entries = edit_list
    table.edit(table.model().index(0, 1))
    table.delegate.editor.setText('renamed')
    qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Tab)
    qtbot.keyClick(table.delegate.editor.phonetic, Qt.Key.Key_Tab)
    assert repo.get(entries[0].id).text == 'renamed'
    assert field(table) == (entries[0].id, -1, 0, 2)
    table.delegate.editor.set_pos('adj.')
    qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Backtab)
    assert repo.get(entries[0].id).senses[0].pos == 'adj.'
    assert field(table) == (entries[0].id, -1, 0, 1)
    assert table.delegate.editor.text() == 'renamed'


@pytest.mark.parametrize('column,step,next_field', [
    (3, 1, (-1, 0, 2)),
    (3, -1, (-1, 0, 1)),
    (1, 1, (-1, 0, 1)),
    (1, -1, (-1, 1, 3)),
])
def test_tab_continues_past_deleted_fields(window, repo, qtbot, column, step, next_field):
    entry = repo.save(WordEntry('main', [Sense('n.', 'first'), Sense('v.', 'second')],
                                [RelatedWord('child', [Sense('n.', 'child')])]))
    following = repo.save(WordEntry('next', [Sense('n.', 'next')]))
    window.reveal(entry.id, True)
    table = window.table
    table.edit(table._entry_index(entry.id, 0 if column == 1 else -1, 0, column))
    editor = table.delegate.editor
    if column == 1:
        editor.setText(' \t ')
        if step == 1:
            qtbot.keyClick(editor, Qt.Key.Key_Tab)
            editor = editor.phonetic
    else:
        editor.setPlainText(' \n ')
    qtbot.keyClick(editor, Qt.Key.Key_Tab if step == 1 else Qt.Key.Key_Backtab)
    destination = following if (column, step) == (1, 1) else entry
    assert field(table) == (destination.id, *next_field)
    saved = repo.get(entry.id)
    if column == 1:
        assert not saved.related and saved.senses == entry.senses
    else:
        assert saved.related == entry.related and saved.senses == entry.senses[1:]


@pytest.mark.parametrize('failure', ['empty', 'duplicate', 'database'])
def test_failed_save_does_not_move_editor(edit_list, repo, qtbot, monkeypatch, failure):
    table, entries = edit_list
    table.edit(table.model().index(0, 1))
    editor = table.delegate.editor
    value = '' if failure == 'empty' else 'beta' if failure == 'duplicate' else 'new name'
    editor.setText(value)
    if failure == 'database':
        def fail(*args):
            raise sqlite3.OperationalError('disk full')
        monkeypatch.setattr(repo, 'save', fail)
    for key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
        qtbot.keyClick(editor, key)
        assert table.delegate.editor is editor and editor.text() == value
        assert field(table) == (entries[0].id, -1, 0, 1)
    assert repo.get(entries[0].id).text == 'alpha'


def test_tab_in_pos_popup_moves_to_definition(edit_list, qtbot):
    table, entries = edit_list
    table.edit(table.model().index(0, 2))
    editor = table.delegate.editor
    editor.showPopup()
    qtbot.keyClick(editor.view(), Qt.Key.Key_Tab)
    assert field(table) == (entries[0].id, -1, 0, 3)


def test_f2_without_selection_does_nothing(edit_list, qtbot):
    table, _ = edit_list
    table.setFocus()
    table.clearSelection()
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert table.delegate.editor is None


def test_f2_can_edit_hidden_search_result(window, repo, qtbot):
    entry = repo.save(WordEntry('alpha', [Sense('n.', 'first')]))
    window.search_box.setText('alpha')
    window.set_global_hidden(True)
    table = window.table
    assert table.model().rows[0].hidden
    table.setCurrentIndex(table.model().index(0, 1))
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == (entry.id, -1, 0, 3)
    table.delegate.cancel()


def test_tab_does_not_edit_wrong_entry_when_saved_name_stops_matching_search(window, repo, qtbot):
    entry = repo.save(WordEntry('alpha', [Sense('n.', 'first')]))
    repo.save(WordEntry('alphabet', [Sense('n.', 'other')]))
    window.search_box.setText('alpha')
    table = window.table
    table.edit(table.model().index(0, 1))
    table.delegate.editor.setText('renamed')
    qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Tab)
    qtbot.keyClick(table.delegate.editor.phonetic, Qt.Key.Key_Tab)
    assert repo.get(entry.id).text == 'renamed'
    assert table.delegate.editor is None
    assert table.model().rows[0].entry.text == 'alphabet'


def test_tab_skips_search_result_removed_by_clearing_translation(window, repo, qtbot):
    entry = repo.save(WordEntry('alpha', [Sense('n.', 'match'), Sense('v.', 'another')]))
    next_entry = repo.save(WordEntry('match', [Sense('n.', 'next')]))
    window.search_box.setText('match')
    table = window.table
    table.edit(table._entry_index(entry.id, -1, 0, 3))
    editor = table.delegate.editor
    editor.clear()
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert repo.get(entry.id).senses == [Sense('v.', 'another')]
    assert field(table) == (next_entry.id, -1, 0, 1)


def test_edit_completion_trims_outer_whitespace_and_preserves_internal_content(edit_list, repo, qtbot):
    table, entries = edit_list
    for owner, column, expected in [
        (-1, 1, 'main  word'), (-1, 3, 'first  line\n\nsecond line'),
        (0, 1, 'related  word'), (0, 3, 'related  first\n\nrelated second'),
    ]:
        target = (entries[0].id, owner, 0, column)
        table.edit_field(target)
        editor = table.delegate.editor
        padded = ' \t\n\r\n ' + expected + ' \n\t\r\n '
        if column == 1:
            editor.setText(padded)
        else:
            editor.setPlainText(padded)
        qtbot.keyClick(editor, Qt.Key.Key_Return)
        saved = repo.get(entries[0].id)
        word = saved if owner == -1 else saved.related[owner]
        assert (word.text if column == 1 else word.senses[0].translation) == expected
        table.delegate.cancel()
        table.edit_field(target)
        reopened = table.delegate.editor
        assert (reopened.text() if column == 1 else reopened.toPlainText()) == expected
        table.delegate.cancel()


def test_new_entry_form_trims_main_related_and_all_senses(window, repo):
    main = window.blocks[0]
    main.word.setText('  main  word\t ')
    main.rows[0].pos.set_pos('n.')
    main.rows[0].translation.setPlainText('\n \t first  line\n\nsecond line \n\t')
    main.add_sense()
    main.rows[1].pos.set_pos('v.')
    main.rows[1].translation.setPlainText(' \n second meaning \t\n')
    window.add_related()
    child = window.blocks[-1]
    child.word.setText(' \t related  word  ')
    child.rows[0].pos.set_pos('n.')
    child.rows[0].translation.setPlainText(' \n related  first\n\nrelated second \n ')
    assert window.save_current()
    saved = repo.list_entries()[0]
    assert saved.text == 'main  word'
    assert [sense.translation for sense in saved.senses] == ['first  line\n\nsecond line', 'second meaning']
    assert saved.related[0].text == 'related  word'
    assert saved.related[0].senses[0].translation == 'related  first\n\nrelated second'

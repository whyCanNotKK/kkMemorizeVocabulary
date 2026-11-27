from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import QEvent, QModelIndex, Qt
from PySide6.QtGui import QCursor, QHelpEvent, QKeyEvent
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
    # Start outside the list so each mouse-driven test performs a real movement.
    control = window.filter_buttons['pending']
    qtbot.mouseMove(control, control.rect().center())
    table.reset_mouse_target()
    yield table, entries
    table.delegate.cancel()


def field(table):
    index = QModelIndex(table.delegate.edit_index)
    row = index.data(Qt.ItemDataRole.UserRole)
    return row.entry.id, row.owner, row.sense, index.column()


@pytest.mark.parametrize('owner,sense,column', [(-1, 0, 1), (-1, 1, 2), (0, 1, 3)])
def test_f2_edits_mouse_target_field(edit_list, qtbot, owner, sense, column):
    table, entries = edit_list
    number = next(i for i, row in enumerate(table.model().rows)
                  if (row.entry.id, row.owner, row.sense) == (entries[0].id, owner, sense))
    table.setCurrentIndex(table.model().index(number, column))
    table.setFocus()
    qtbot.mouseMove(table.viewport(), table.visualRect(table.currentIndex()).center())
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == (entries[0].id, owner, sense, column)


def test_f2_keeps_hidden_entry_unedited(edit_list, repo, qtbot):
    table, entries = edit_list
    table.revealRequested.emit(entries[0].id, False)
    table.setCurrentIndex(table.model().index(0, 1))
    table.setFocus()
    before = repo.study_backup()
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert table.delegate.editor is None
    assert table.model().rows[0].hidden
    assert repo.study_backup() == before


def test_hover_within_selected_group_aims_f2_at_main_or_related_definition(edit_list, qtbot):
    table, entries = edit_list
    table.select_entry(entries[0].id)
    table.setFocus()
    for owner, sense in [(-1, 1), (0, 0), (0, 1), (-1, 0)]:
        target = table._entry_index(entries[0].id, owner, sense, 3)
        point = table.visualRect(target).center()
        position = table.verticalScrollBar().value()
        qtbot.mouseMove(table.viewport(), point)
        assert table.currentIndex() == target
        assert table.hasFocus() and table.verticalScrollBar().value() == position
        assert {index.data(Qt.ItemDataRole.UserRole).entry.id
                for index in table.selectionModel().selectedRows()} == {entries[0].id}
        qtbot.keyClick(table, Qt.Key.Key_F2)
        assert field(table) == (entries[0].id, owner, sense, 3)
        table.delegate.cancel()
        table.setFocus()
        qtbot.wait(10)  # Let the closed editor finish returning keyboard focus.


def test_tooltip_aims_f2_without_crossing_groups_or_stealing_input_focus(edit_list, window, qtbot):
    table, entries = edit_list
    table.set_entry_revealed(entries[1].id, True)
    table.select_entry(entries[0].id)
    table.setFocus()
    original = table.currentIndex()

    def tooltip(index):
        point = table.visualRect(index).center()
        QApplication.sendEvent(table.viewport(), QHelpEvent(
            QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))

    other = table._entry_index(entries[1].id, 0, 1, 3)
    tooltip(other)
    assert table.currentIndex() == original
    target = table._entry_index(entries[0].id, 0, 1, 3)
    control = window.study_page.refresh_button if table.study_mode else window.search_box
    control.setFocus()
    tooltip(target)
    assert control.hasFocus() and table.currentIndex() == original
    table.setFocus()
    table.clearSelection()
    tooltip(target)
    assert table.currentIndex() == original and not table.selectionModel().hasSelection()
    table.select_entry(entries[0].id)
    tooltip(target)
    assert table.currentIndex() == original  # A tooltip cannot re-enable mouse targeting.
    qtbot.mouseMove(table.viewport(), table.visualRect(target).center())
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == (entries[0].id, 0, 1, 3)
    # Hovering another row never submits or moves an open editor.
    editor = table.delegate.editor
    tooltip(table._entry_index(entries[0].id, -1, 0, 3))
    assert table.delegate.editor is editor and field(table) == (entries[0].id, 0, 1, 3)


@pytest.mark.parametrize('location', ['other-group', 'empty-list-area', 'toolbar'])
def test_f2_uses_other_groups_editable_mouse_target_or_selected_main_fallback(edit_list, window, qtbot, location):
    table, entries = edit_list
    table.set_entry_revealed(entries[1].id, True)
    table.select_entry(entries[0].id)
    table.setFocus()
    previous = table._entry_index(entries[0].id, owner=0, sense=1, column=3)
    qtbot.mouseMove(table.viewport(), table.visualRect(previous).center())
    assert table.currentIndex() == previous
    if location == 'other-group':
        other = table._entry_index(entries[1].id, owner=0, sense=1, column=3)
        qtbot.mouseMove(table.viewport(), table.visualRect(other).center())
    elif location == 'empty-list-area':
        qtbot.mouseMove(table.viewport(), table.viewport().rect().bottomRight())
    else:
        control = window.filter_buttons['all']
        qtbot.mouseMove(control, control.rect().center())
    assert table.hasFocus() and table.currentIndex() == previous
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == ((entries[1].id, 0, 1, 3) if location == 'other-group'
                            else (entries[0].id, -1, 0, 3))


@pytest.mark.parametrize('operation', ['down', 'up', 'tab', 'switch'])
def test_keyboard_location_change_disables_stationary_mouse_target(edit_list, window, qtbot, operation):
    table, entries = edit_list
    table.set_entry_revealed(entries[1].id, True)
    table.select_entry(entries[0].id)
    table.setFocus()
    target = table._entry_index(entries[0].id, owner=0, sense=1, column=3)
    qtbot.mouseMove(table.viewport(), table.visualRect(target).center())
    assert table._mouse_moved
    if operation == 'switch':
        number = 1 if window.filter_name == 'all' else 2
        qtbot.keyClick(table, getattr(Qt.Key, f'Key_{number}'), Qt.KeyboardModifier.AltModifier)
        table = window.current_table()
    elif operation == 'tab':
        qtbot.keyClick(table, Qt.Key.Key_Tab)
        assert not table.hasFocus()
        table.setFocus()
    else:
        qtbot.keyClick(table, Qt.Key.Key_Down if operation == 'down' else Qt.Key.Key_Up)
    assert not table._mouse_moved
    selected = table.currentIndex().data(Qt.ItemDataRole.UserRole)
    assert selected is not None
    # A tooltip at the old mouse location must not take keyboard control back.
    point = table.viewport().mapFromGlobal(QCursor.pos())
    QApplication.sendEvent(table.viewport(), QHelpEvent(
        QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))
    assert not table._mouse_moved
    qtbot.keyClick(table, Qt.Key.Key_F2)
    if selected.hidden:
        assert table.delegate.editor is None
    else:
        assert field(table) == (selected.entry.id, -1, 0, 3)


def test_mouse_target_allows_hidden_spelling_but_not_hidden_definition(edit_list, qtbot):
    table, entries = edit_list
    table.set_entry_revealed(entries[0].id, False)
    table.select_entry(entries[0].id)
    table.setFocus()
    index = table._entry_index(entries[0].id)
    qtbot.mouseMove(table.viewport(), table.visualRect(index).center())
    assert table._mouse_moved
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == (entries[0].id, -1, 0, 1)
    assert table.model().rows[0].hidden
    table.delegate.cancel()
    table.setFocus()
    qtbot.wait(10)
    table.set_entry_revealed(entries[0].id, False)
    index = table._entry_index(entries[0].id, column=2)
    qtbot.mouseMove(table.viewport(), table.visualRect(index).center())
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert table.delegate.editor is None and table.model().rows[0].hidden


@pytest.mark.parametrize('revealed', [False, True])
def test_f2_spelling_and_phonetic_edit_preserves_answer_visibility(edit_list, repo, qtbot, revealed):
    table, entries = edit_list
    table.set_entry_revealed(entries[0].id, revealed)
    table.select_entry(entries[0].id)
    table.setFocus()
    before = repo.study_backup()
    index = table._entry_index(entries[0].id)
    qtbot.mouseMove(table.viewport(), table.visualRect(index).center())
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert field(table) == (entries[0].id, -1, 0, 1)

    def assert_visibility():
        rows = [row for row in table.model().rows if row.entry.id == entries[0].id]
        assert rows and all(row.hidden == (not revealed) for row in rows)
        assert len(rows) == (4 if revealed else 1)

    assert_visibility()
    editor = table.delegate.editor
    editor.setText('renamed')
    editor.phonetic.setText('/new/')
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.wait(10)
    assert_visibility()
    saved = repo.get(entries[0].id)
    assert saved.text == 'renamed' and saved.phonetic == '/new/'
    assert list(repo.study_backup().records.values()) == list(before.records.values())


@pytest.mark.parametrize('source,target_column', [('word', 3), ('phonetic', 1), ('translation', 2), ('pos', 3)])
def test_f2_saves_active_editor_then_edits_mouse_target(edit_list, repo, qtbot, source, target_column):
    table, entries = edit_list
    table.set_entry_revealed(entries[1].id, True)
    before = repo.study_backup()
    column = {'word': 1, 'phonetic': 1, 'translation': 3, 'pos': 2}[source]
    index = table._entry_index(entries[0].id, column=column)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    if source == 'word':
        editor.setText('renamed')
    elif source == 'phonetic':
        editor.phonetic.setText('/new/')
        editor.phonetic.setFocus()
    elif source == 'translation':
        editor.setPlainText('new meaning')
    else:
        editor.set_pos('adj.')
    qtbot.wait(10)
    target = table._entry_index(entries[1].id, owner=0, column=target_column)
    qtbot.mouseMove(table.viewport(), table.visualRect(target).center())
    assert table._mouse_moved and table.delegate.editor is editor
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_F2)
    qtbot.wait(10)
    saved = repo.get(entries[0].id)
    assert {'word': saved.text, 'phonetic': saved.phonetic,
            'translation': saved.senses[0].translation, 'pos': saved.senses[0].pos}[source] == {
                'word': 'renamed', 'phonetic': '/new/', 'translation': 'new meaning', 'pos': 'adj.'}[source]
    assert field(table) == (entries[1].id, 0, 0, target_column)
    assert table.delegate.editor is not editor
    assert list(repo.study_backup().records.values()) == list(before.records.values())


def test_f2_failed_save_keeps_original_editor_and_repeated_f2_does_not_submit(edit_list, repo, qtbot):
    table, entries = edit_list
    table.setCurrentIndex(table._entry_index(entries[0].id))
    table.edit(table.currentIndex())
    editor = table.delegate.editor
    editor.setText('')  # A main word with a related word cannot have an empty spelling.
    target = table._entry_index(entries[0].id, owner=0, column=3)
    qtbot.mouseMove(table.viewport(), table.visualRect(target).center())
    qtbot.keyClick(editor, Qt.Key.Key_F2)
    qtbot.wait(10)
    assert table.delegate.editor is editor and editor.text() == '' and editor.hasFocus()
    assert repo.get(entries[0].id) == entries[0]
    editor.setText('renamed')
    QApplication.sendEvent(editor, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F2,
                                           Qt.KeyboardModifier.NoModifier, '', True))
    assert table.delegate.editor is editor and repo.get(entries[0].id) == entries[0]


def test_f2_maps_mouse_target_when_saving_removes_an_earlier_sense(edit_list, repo, qtbot):
    table, entries = edit_list
    table.setCurrentIndex(table._entry_index(entries[0].id, column=3))
    table.edit(table.currentIndex())
    editor = table.delegate.editor
    editor.clear()
    target = table._entry_index(entries[0].id, sense=1, column=3)
    qtbot.mouseMove(table.viewport(), table.visualRect(target).center())
    qtbot.keyClick(editor, Qt.Key.Key_F2)
    assert repo.get(entries[0].id).senses == [Sense('v.', 'second')]
    assert field(table) == (entries[0].id, -1, 0, 3)
    assert table.delegate.editor.toPlainText() == 'second'


def test_f2_saves_without_editing_a_different_field_when_mouse_target_is_removed(edit_list, repo, qtbot):
    table, entries = edit_list
    table.setCurrentIndex(table._entry_index(entries[0].id, column=3))
    table.edit(table.currentIndex())
    editor = table.delegate.editor
    editor.clear()
    qtbot.mouseMove(editor.viewport(), editor.viewport().rect().center())
    assert table._mouse_moved
    qtbot.keyClick(editor, Qt.Key.Key_F2)
    assert repo.get(entries[0].id).senses == [Sense('v.', 'second')]
    assert table.delegate.editor is None


def test_f2_during_edit_without_mouse_movement_saves_then_uses_main_translation(edit_list, repo, qtbot):
    table, entries = edit_list
    table.setCurrentIndex(table._entry_index(entries[0].id, owner=0))
    table.edit(table.currentIndex())
    editor = table.delegate.editor
    editor.setText('renamed child')
    editor.phonetic.setText('/child/')
    assert not table._mouse_moved
    qtbot.keyClick(editor, Qt.Key.Key_F2)
    saved = repo.get(entries[0].id).related[0]
    assert saved.text == 'renamed child' and saved.phonetic == '/child/'
    assert field(table) == (entries[0].id, -1, 0, 3)


def test_f2_storage_failure_keeps_editor_and_unsaved_text(edit_list, repo, qtbot, monkeypatch):
    table, entries = edit_list
    table.setCurrentIndex(table._entry_index(entries[0].id, column=3))
    table.edit(table.currentIndex())
    editor = table.delegate.editor
    editor.setPlainText('unsaved meaning')
    target = table._entry_index(entries[0].id, owner=0, column=1)
    qtbot.mouseMove(table.viewport(), table.visualRect(target).center())

    def fail(*args):
        raise OSError('disk unavailable')

    monkeypatch.setattr(repo, 'save', fail)
    qtbot.keyClick(editor, Qt.Key.Key_F2)
    qtbot.wait(10)
    assert table.delegate.editor is editor and editor.hasFocus()
    assert editor.toPlainText() == 'unsaved meaning' and repo.get(entries[0].id) == entries[0]


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
        monkeypatch.setattr(repo, 'save_links', fail)
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


def test_search_cannot_hide_results_and_f2_can_edit_translation(window, repo, qtbot):
    entry = repo.save(WordEntry('alpha', [Sense('n.', 'first')]))
    window.search_box.setText('alpha')
    window.set_global_hidden(True)
    table = window.table
    assert not table.model().rows[0].hidden
    assert window.global_hide_button.isHidden()
    table.setCurrentIndex(table.model().index(0, 1))
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_F2)
    assert table.delegate.editor is not None
    assert field(table) == (entry.id, -1, 0, 3)
    assert not table.model().rows[0].hidden
    table.delegate.editor.setPlainText('updated meaning')
    qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Return)
    assert repo.get(entry.id).senses[0].translation == 'updated meaning'
    assert all(not row.hidden for row in table.model().rows)


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
    # Both results must match by translation so search ranking keeps alpha first.
    next_entry = repo.save(WordEntry('next', [Sense('n.', 'match')]))
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

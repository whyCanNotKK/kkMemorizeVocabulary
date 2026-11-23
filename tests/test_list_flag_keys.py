from datetime import date

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository


def seed(window, repo):
    entry = repo.save(WordEntry('word', [Sense('n.', 'meaning'), Sense('v.', 'another')],
                               [RelatedWord('child', [Sense('n.', 'child meaning')])]))
    window.refresh()
    window.table.select_entry(entry.id)
    window.table.setFocus()
    return entry


def test_mark_learned_focuses_original_next_word(window, repo, qtbot):
    entries = [repo.save(WordEntry(text, [Sense('n.', 'meaning')]))
               for text in ('first', 'second')]
    window.refresh()
    table = window.table
    table.select_entry(entries[0].id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_F4)
    assert repo.get(entries[0].id).learned
    assert table.hasFocus()
    current = table.currentIndex()
    assert current.isValid()
    assert current.data(Qt.ItemDataRole.UserRole).entry.id == entries[1].id
    assert table.selectionModel().isSelected(current)


@pytest.mark.parametrize('page,trigger,position', [
    ('pending', 'button', 1), ('learned', 'f4', 1), ('learned', 'button', 2),
    ('all', 'f4', 0), ('all', 'button', 1), ('search', 'f4', 0),
    ('search', 'button', 1), ('pending', 'f4', 2),
])
def test_mark_status_uses_original_list_order_for_focus(
        window, repo, qtbot, page, trigger, position):
    entries = [repo.save(WordEntry(text, [Sense('n.', 'meaning'), Sense('v.', 'another')],
                                   [RelatedWord('child', [Sense('n.', 'child meaning')])],
                                   learned=page == 'learned'))
               for text in ('last match', 'match first', 'x match')]
    window.set_filter('all' if page == 'search' else page)
    if page == 'search':
        window.search_box.setText('match')
    table = window.table
    entry_ids = [row.entry.id for row in table.model().rows if row.first]
    source = entry_ids[position]
    target = entry_ids[position + 1 if position + 1 < len(entry_ids) else position - 1]
    # Navigate from a child row, or click another word's button with the form focused.
    table.set_entry_revealed(source, True)
    table.setCurrentIndex(table._entry_index(source, owner=0))
    hidden = table._entry_index(target).data(Qt.ItemDataRole.UserRole).hidden
    if trigger == 'f4':
        table.setFocus()
        qtbot.keyClick(table, Qt.Key.Key_F4)
    else:
        window.blocks[0].word.setFocus()
        index = table._entry_index(source, column=table.action_column)
        table.scrollTo(index)
        rect = dict(table.action_rects(table.visualRect(index), index.data(Qt.ItemDataRole.UserRole)))['learned']
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert repo.get(source).learned != (page == 'learned')
    assert table.hasFocus()
    current = table.currentIndex()
    assert current.data(Qt.ItemDataRole.UserRole).entry.id == target
    assert table.selectionModel().isSelected(current)
    rows = [i for i, row in enumerate(table.model().rows) if row.entry.id == target]
    assert all(table.selectionModel().isRowSelected(row, current.parent()) for row in rows)
    assert current.data(Qt.ItemDataRole.UserRole).hidden == hidden


@pytest.mark.parametrize('page', ['pending', 'all'])
def test_status_change_of_only_word_keeps_list_focus(window, repo, qtbot, page):
    entry = seed(window, repo)
    window.set_filter(page)
    table = window.table
    table.select_entry(entry.id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_F4)
    assert table.hasFocus()
    if page == 'all':
        assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id
        assert table.selectionModel().isSelected(table.currentIndex())
    else:
        assert not table.model().rows and not table.currentIndex().isValid()


@pytest.mark.parametrize('page,owner,sense', [
    ('pending', -1, 0), ('all', 0, 0), ('learned', -1, 1), ('search', -1, 0),
])
def test_flag_keys_update_selected_main_word_and_persist(window, repo, qtbot, page, owner, sense):
    entry = seed(window, repo)
    if page == 'learned':
        repo.set_flag(entry.id, 'learned', True)
    window.set_filter('all' if page == 'search' else page)
    if page == 'search':
        window.search_box.setText('word')
    if owner >= 0 or sense:
        window.set_global_hidden(False)
    table = window.table
    table.setCurrentIndex(table._entry_index(entry.id, owner, sense, 1))
    table.setFocus()
    hidden = table.model().rows[0].hidden
    for key, modifier, important in (
        (Qt.Key.Key_F1, Qt.KeyboardModifier.NoModifier, False),
        (Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier, True),
    ):
        qtbot.keyClick(table, key, modifier)
        assert repo.get(entry.id).important == important
        index = table._entry_index(entry.id, column=0)
        assert index.data(Qt.ItemDataRole.AccessibleTextRole) == ('重点' if important else '非重点')
        assert table.model().rows[0].hidden == hidden
    qtbot.keyClick(table, Qt.Key.Key_F4)
    learned = page != 'learned'
    assert repo.get(entry.id).learned == learned
    if page in ('pending', 'learned'):
        assert not table.model().rows
    else:
        assert table.model().rows[0].entry.learned == learned
    # A separate connection observes committed state, as it would after restarting.
    reopened = Repository(repo.path)
    try:
        assert reopened.get(entry.id).learned == learned
        assert reopened.get(entry.id).important
    finally:
        reopened.close()
    window.set_filter('all')
    table.select_entry(entry.id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_F4)
    assert repo.get(entry.id).learned != learned


def test_remapped_important_shortcut_only_updates_selected_list_word(window, repo, qtbot):
    entry = seed(window, repo)
    draft = window.form_value()
    window.shortcuts.update(dict(window.shortcuts.values, important='Alt+I'))
    qtbot.keyClick(window.table, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)
    assert repo.get(entry.id).important
    qtbot.keyClick(window.table, Qt.Key.Key_I, Qt.KeyboardModifier.AltModifier)
    assert not repo.get(entry.id).important
    assert window.form_value() == draft
    qtbot.keyClick(window.table, Qt.Key.Key_F1)
    assert repo.get(entry.id).important


def test_important_chord_respects_list_selection_editing_drag_busy_and_study(window, repo, qtbot):
    entry = seed(window, repo)
    table = window.table
    def press(control):
        qtbot.keyClick(control, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)
        assert repo.get(entry.id) == entry

    table.clearSelection()
    press(table)
    table.select_entry(entry.id)
    QApplication.sendEvent(table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_I,
                                          Qt.KeyboardModifier.ControlModifier, '', True))
    assert repo.get(entry.id) == entry
    table._drag_candidate = (entry.id, table.rect().center())
    press(table)
    table.cancel_drag()
    with window.busy_overlay.display('testing'):
        press(table)
    table.edit(table._entry_index(entry.id))
    press(table.delegate.editor)
    table.delegate.cancel()
    window.set_filter('study17')
    window.study_page.table.select_entry(entry.id)
    window.study_page.table.setFocus()
    press(window.study_page.table)


@pytest.mark.parametrize('key', [Qt.Key.Key_F1, Qt.Key.Key_F4])
def test_flag_keys_ignore_unselected_modified_repeated_dragging_and_busy_input(
        window, repo, qtbot, key):
    entry = seed(window, repo)
    table = window.table
    for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier,
                     Qt.KeyboardModifier.MetaModifier):
        qtbot.keyClick(table, key, modifier)
    QApplication.sendEvent(table, QKeyEvent(QEvent.Type.KeyPress, key,
                                          Qt.KeyboardModifier.NoModifier, '', True))
    table.clearSelection()
    qtbot.keyClick(table, key)
    table.select_entry(entry.id)
    table._drag_candidate = (entry.id, table.rect().center())
    qtbot.keyClick(table, key)
    table.cancel_drag()
    with window.busy_overlay.display('testing'):
        qtbot.keyClick(table, key)
    assert repo.get(entry.id) == entry


@pytest.mark.parametrize('key', [Qt.Key.Key_F1, Qt.Key.Key_F4])
def test_flag_keys_ignore_editors_search_form_and_popups(window, repo, qtbot, key):
    entry = seed(window, repo)
    table = window.table
    table.edit(table._entry_index(entry.id))
    editor = table.delegate.editor
    qtbot.keyClick(editor, key)
    assert table.delegate.editor is editor
    table.delegate.cancel()
    for control in (window.search_box, window.blocks[0].word):
        control.setFocus()
        qtbot.keyClick(control, key)
    for popup in (QDialog(window), QMenu(window)):
        qtbot.addWidget(popup)
        table.setFocus()
        if isinstance(popup, QMenu):
            popup.addAction('test')
            popup.popup(table.mapToGlobal(table.rect().center()))
        else:
            popup.setModal(True)
            popup.show()
        qtbot.keyClick(popup, key)
        popup.close()
    assert repo.get(entry.id) == entry


def test_flag_keys_do_not_change_study_progress_or_normal_flags(window, repo, qtbot, monkeypatch):
    entry = seed(window, repo)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter('study17')
    table = window.study_page.table
    table.select_entry(entry.id)
    table.setFocus()
    for revealed in (False, True):
        table.set_entry_revealed(entry.id, revealed)
        before = repo.study_backup()
        for key in (Qt.Key.Key_F1, Qt.Key.Key_F4):
            qtbot.keyClick(table, key)
            assert table.model().rows[0].hidden != revealed
            assert repo.get(entry.id) == entry
            assert repo.study_backup() == before


@pytest.mark.parametrize('key', [Qt.Key.Key_F1, Qt.Key.Key_F4])
def test_flag_write_failure_preserves_list_and_draft(window, repo, qtbot, monkeypatch, key):
    entry = seed(window, repo)
    window.reset_form(entry)
    window.blocks[0].word.setText('draft')
    table = window.table
    table.setFocus()
    before = table.view_state()

    def fail(*args):
        raise OSError('write failed')

    notices = []
    monkeypatch.setattr(repo, 'set_flag', fail)
    monkeypatch.setattr(window, 'show_notice', lambda *args: notices.append(args))
    qtbot.keyClick(table, key)
    assert repo.get(entry.id) == entry
    assert table.model().rows[0].entry == entry
    assert table.view_state() == before
    assert window.blocks[0].word.text() == 'draft'
    assert notices == [('write failed', True)]

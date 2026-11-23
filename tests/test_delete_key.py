from datetime import date

import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.draft_reset_dialog import DraftResetDialog


def seed(window, repo):
    entry = repo.save(WordEntry('word', [Sense('n.', 'meaning'), Sense('v.', 'another')],
                               [RelatedWord('child', [Sense('n.', 'child meaning')])]))
    window.refresh()
    window.table.select_entry(entry.id)
    window.table.setFocus()
    return entry


@pytest.mark.parametrize('page,trigger,position', [
    ('pending', 'del', 0), ('pending', 'icon', 1), ('learned', 'del', 2),
    ('all', 'icon', 1), ('search', 'del', 0), ('search', 'icon', 1),
])
def test_removal_focuses_original_neighbor(window, repo, qtbot, monkeypatch, page, trigger, position):
    for text in ('last match', 'match first', 'x match'):
        repo.save(WordEntry(text, [Sense('n.', 'meaning'), Sense('v.', 'another')],
                            [RelatedWord('child', [Sense('n.', 'child meaning')])],
                            learned=page == 'learned'))
    window.set_filter('all' if page == 'search' else page)
    if page == 'search':
        window.search_box.setText('match')
    table = window.table
    entry_ids = [row.entry.id for row in table.model().rows if row.first]
    source = entry_ids[position]
    target = entry_ids[position + 1 if position + 1 < len(entry_ids) else position - 1]
    table.set_entry_revealed(source, True)
    table.setCurrentIndex(table._entry_index(source, owner=0))
    hidden = table._entry_index(target).data(Qt.ItemDataRole.UserRole).hidden
    if page == 'pending' and trigger == 'del':
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
    else:
        monkeypatch.setattr(window, 'confirm_word_removal', lambda entry_id: True)
    if trigger == 'del':
        table.setFocus()
        qtbot.keyClick(table, Qt.Key.Key_Delete)
    else:
        window.blocks[0].word.setFocus()
        index = table._entry_index(source, column=table.action_column)
        table.scrollTo(index)
        rect = dict(table.action_rects(table.visualRect(index), index.data(Qt.ItemDataRole.UserRole)))['trash']
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert source not in [entry.id for entry in repo.list_entries()]
    qtbot.wait(20)
    assert table.hasFocus()
    current = table.currentIndex()
    assert current.isValid()
    assert current.data(Qt.ItemDataRole.UserRole).entry.id == target
    assert table.selectionModel().isSelected(current)
    assert current.data(Qt.ItemDataRole.UserRole).hidden == hidden


@pytest.mark.parametrize('trigger,choice', [
    ('del', 'enter'), ('del', 'escape'), ('del', 'tab-confirm'),
    ('icon', 'confirm'), ('icon', 'cancel'), ('icon', 'close'),
])
def test_word_removal_uses_compact_confirmation_with_cancel_default(
        window, repo, qtbot, tmp_path, trigger, choice):
    entry = seed(window, repo)
    context = StudyContext(date(2026, 11, 5), False)
    repo.prepare_study(context)
    before = repo.study_backup()
    errors = []

    def respond():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, DraftResetDialog)
            assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
            assert dialog.findChild(QLabel).text() == '同时还会从十七天背单词记录中删除(若存在)，确认移除吗？'
            assert {button.text() for button in dialog.findChildren(QPushButton)} == {'确认', '取消'}
            qtbot.waitUntil(dialog.isActiveWindow)
            assert dialog.cancel_button.hasFocus() and dialog.cancel_button.isDefault()
            assert dialog.cancel_button.width() >= 72
            dialog.grab().save(str(tmp_path / 'word-removal.png'))
            if choice == 'tab-confirm':
                qtbot.keyClick(dialog.cancel_button, Qt.Key.Key_Tab)
                assert dialog.confirm_button.hasFocus()
                qtbot.keyClick(dialog.confirm_button, Qt.Key.Key_Return)
            elif choice in ('confirm', 'cancel'):
                qtbot.mouseClick(dialog.confirm_button if choice == 'confirm' else dialog.cancel_button,
                                 Qt.MouseButton.LeftButton)
            elif choice == 'close':
                dialog.close()
            else:
                qtbot.keyClick(dialog.cancel_button, Qt.Key.Key_Return if choice == 'enter' else Qt.Key.Key_Escape)
        except BaseException as error:
            errors.append(error)
        finally:
            if dialog is not None and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, respond)
    if trigger == 'del':
        qtbot.keyClick(window.table, Qt.Key.Key_Delete)
    else:
        table = window.table
        if choice == 'confirm':
            window.blocks[0].word.setFocus()
        index = table._entry_index(entry.id, column=table.action_column)
        rect = dict(table.action_rects(table.visualRect(index), index.data(Qt.ItemDataRole.UserRole)))['trash']
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert not errors, errors
    qtbot.waitUntil(window.table.hasFocus)
    if choice in ('confirm', 'tab-confirm'):
        assert not repo.list_entries() and not repo.study_backup().records
        assert not window.table.currentIndex().isValid()
    else:
        assert repo.get(entry.id) == entry and repo.study_backup() == before
        current = window.table.currentIndex()
        assert current.data(Qt.ItemDataRole.UserRole).entry.id == entry.id
        assert window.table.selectionModel().isSelected(current)


def test_removal_failure_keeps_selection(window, repo, qtbot, monkeypatch):
    entry = seed(window, repo)
    repo.save(WordEntry('next', [Sense('n.', 'meaning')]))
    window.refresh()
    table = window.table
    before = table.view_state()
    monkeypatch.setattr(window, 'confirm_word_removal', lambda entry_id: True)

    def fail(*args):
        raise OSError('delete failed')

    notices = []
    monkeypatch.setattr(repo, 'delete', fail)
    monkeypatch.setattr(window, 'show_notice', lambda *args: notices.append(args))
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    assert table.hasFocus() and table.view_state() == before
    assert repo.get(entry.id) == entry
    assert notices == [('delete failed', True)]


@pytest.mark.parametrize('page,owner,sense,confirmed', [
    ('pending', -1, 0, True), ('all', 0, 0, True), ('learned', -1, 1, True),
    ('search', -1, 0, True), ('pending', 0, 0, False),
])
def test_del_uses_trash_confirmation_for_selected_words_whole_group(
        window, repo, qtbot, monkeypatch, page, owner, sense, confirmed):
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
    confirmations = []

    def confirm(entry_id):
        confirmations.append(entry_id)
        return confirmed

    monkeypatch.setattr(window, 'confirm_word_removal', confirm)
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    assert confirmations == [entry.id]
    assert bool(repo.list_entries()) == (not confirmed)
    for name in ('senses', 'related_words'):
        assert bool(repo.connection.execute(f'SELECT count(*) FROM {name}').fetchone()[0]) == (not confirmed)


def test_del_rejects_unselected_modified_repeated_and_dragging_input(window, repo, qtbot, monkeypatch):
    entry = seed(window, repo)
    monkeypatch.setattr(window, 'confirm_word_removal', lambda *args: pytest.fail('must not delete'))
    table = window.table
    for modifiers in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.AltModifier,
                      Qt.KeyboardModifier.ShiftModifier):
        qtbot.keyClick(table, Qt.Key.Key_Delete, modifiers)
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, '', True)
    QApplication.sendEvent(table, repeat)
    table.clearSelection()
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    table.select_entry(entry.id)
    table._drag_candidate = (entry.id, table.rect().center())
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    table.cancel_drag()
    assert repo.get(entry.id) == entry


def test_del_in_editors_and_search_deletes_text_only(window, repo, qtbot, monkeypatch):
    entry = seed(window, repo)
    monkeypatch.setattr(window, 'confirm_word_removal', lambda *args: pytest.fail('must not delete'))
    window.table.edit(window.table._entry_index(entry.id))
    editor = window.table.delegate.editor
    editor.setCursorPosition(0)
    qtbot.keyClick(editor, Qt.Key.Key_Delete)
    assert editor.text() == 'ord' and repo.get(entry.id) == entry
    window.table.delegate.cancel()
    for editor in (window.search_box, window.blocks[0].word):
        editor.setText('word')
        editor.setFocus()
        editor.setCursorPosition(0)
        qtbot.keyClick(editor, Qt.Key.Key_Delete)
        assert editor.text() == 'ord' and repo.get(entry.id) == entry


def test_del_is_disabled_on_study_page(window, repo, qtbot, monkeypatch):
    entry = seed(window, repo)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    monkeypatch.setattr(window, 'confirm_word_removal', lambda *args: pytest.fail('must not delete'))
    window.set_filter('study17')
    table = window.study_page.table
    table.select_entry(entry.id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    assert repo.get(entry.id) == entry


def test_keypad_del_and_dirty_draft_use_existing_trash_guards(window, repo, qtbot, monkeypatch):
    entry = seed(window, repo)
    window.reset_form(entry)
    window.blocks[0].word.setText('draft')
    window.table.setFocus()
    resolutions = []
    monkeypatch.setattr(window, 'resolve_dirty', lambda: resolutions.append(True) or False)
    monkeypatch.setattr(window, 'confirm_word_removal', lambda *args: pytest.fail('draft blocks delete'))
    qtbot.keyClick(window.table, Qt.Key.Key_Delete, Qt.KeyboardModifier.KeypadModifier)
    assert resolutions == [True]
    assert repo.get(entry.id) == entry and window.blocks[0].word.text() == 'draft'

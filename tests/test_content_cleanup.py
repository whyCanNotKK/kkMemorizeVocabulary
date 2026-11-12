from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QMessageBox

from vocabulary.domain import CLEANUP_COMMANDS, RelatedWord, Sense, WordEntry
from vocabulary.draft_reset_dialog import DraftResetDialog
from vocabulary.learning import StudyContext, StudyDrawSettings
from vocabulary.shortcuts import SettingsDialog
from vocabulary.cleanup_input import CleanupInput
from vocabulary.storage import Repository


AM = StudyContext(date(2026, 11, 5), False)


@pytest.mark.parametrize('command', CLEANUP_COMMANDS)
def test_last_hint_character_is_drawn_after_typed_text(qtbot, qapp, command):
    from vocabulary.theme import apply_theme

    apply_theme(qapp)
    prefix = command[:-1]
    hint = CleanupInput()
    reference = QLineEdit()
    for editor, text in ((hint, prefix), (reference, command)):
        qtbot.addWidget(editor)
        editor.setFixedSize(330, 36)
        editor.setText(text)
        editor.show()
        editor.clearFocus()
    qapp.processEvents()
    hint_image = hint.grab().toImage()
    reference_image = reference.grab().toImage()
    # Find the final glyph in a normal Qt input, then require ghost ink in the
    # same columns. This catches overlap with the typed prefix, not just slicing.
    reference_columns = []
    for x in range(reference.width() - 12):
        if any(reference_image.pixelColor(x, y).red() > 150 for y in range(12, 25)):
            reference_columns.append(x)
    last_x = max(reference_columns)
    ghost_pixels = sum(hint_image.pixelColor(x, y).red() > 65
                       for x in range(last_x - 2, last_x + 1) for y in range(12, 25))
    assert ghost_pixels >= 3, f'Final hint character {command[-1]!r} overlaps typed text'
    assert hint.text() == prefix and hint.hint_suffix() == command[-1]


def seed(repo):
    entries = [repo.save(WordEntry(name, [Sense('n.', '释义')],
               [RelatedWord(name + '-related', [Sense('n.', '关联释义')])]))
               for name in ('active', 'finished', 'unused')]
    repo.write_study_draw_settings(StudyDrawSettings(1, 2))
    repo.prepare_study(AM)
    with repo.connection:
        repo.connection.execute('DELETE FROM memorize_vocabulary_17day')
        repo.connection.execute('''INSERT INTO memorize_vocabulary_17day
            (word_id,group_number,stage) VALUES (?,1,1)''', (entries[0].id,))
        repo.connection.execute('''INSERT INTO memorize_vocabulary_17day
            (word_id,group_number,stage,last_completed_on)
            VALUES (?,2,0,'2026-11-04')''', (entries[1].id,))
    repo.start_study_task(AM)
    return entries


@pytest.mark.parametrize('command', CLEANUP_COMMANDS)
def test_cleanup_scope_dependencies_settings_and_restart(repo, command):
    seed(repo)
    before = repo.study_backup()
    shortcuts = repo.shortcuts()
    repo.clear_content(command)
    reopened = Repository(repo.path)
    try:
        names = [entry.text for entry in reopened.list_entries()]
        assert names == ([] if command == 'remove all' else
                         ['active', 'finished'] if command == 'clean vocabulary only' else
                         ['active', 'finished', 'unused'])
        backup = reopened.study_backup()
        if command == 'clean vocabulary only':
            assert backup == before
        else:
            assert not backup.records and not backup.task_history
            assert backup.study_last_add_new_vocabulary is None and backup.group_counter == 0
            assert not reopened.connection.execute('''SELECT * FROM settings WHERE key IN
                ('study_last_add_new_vocabulary','study17.group_counter')''').fetchall()
        assert reopened.study_draw_settings() == StudyDrawSettings(1, 2)
        assert reopened.shortcuts() == shortcuts
        assert reopened.connection.execute('SELECT COUNT(*) FROM related_words').fetchone()[0] == len(names)
        assert reopened.connection.execute('SELECT COUNT(*) FROM senses').fetchone()[0] == 2 * len(names)
        assert not reopened.connection.execute('PRAGMA foreign_key_check').fetchall()
    finally:
        reopened.close()


@pytest.mark.parametrize('command', ['delete study record', 'remove all'])
def test_cleanup_rollback_includes_words_records_history_and_settings(repo, command):
    seed(repo)
    before = repo.study_backup()
    words = repo.list_entries()
    counters = repo.max_pending_sort_order, repo.max_learned_sort_order
    repo.connection.execute('''CREATE TRIGGER reject_clear BEFORE DELETE ON settings
        WHEN OLD.key='study17.group_counter' BEGIN SELECT RAISE(ABORT,'blocked'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='blocked'):
        repo.clear_content(command)
    assert repo.list_entries() == words
    assert repo.study_backup() == before
    assert (repo.max_pending_sort_order, repo.max_learned_sort_order) == counters


def test_cleanup_invalid_command_never_deletes(repo):
    seed(repo)
    before = repo.study_backup()
    for command in ('remove all ', 'Remove all', '', 'del'):
        with pytest.raises(ValueError):
            repo.clear_content(command)
    assert repo.study_backup() == before


def test_hint_is_visual_only_and_confirmation_requires_exact_command(window, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    editor = dialog.cleanup_input
    dialog.scroll.ensureWidgetVisible(editor)
    editor.setFocus()
    qtbot.keyClicks(editor, 'del')
    assert editor.text() == 'del'
    assert editor.hint_suffix() == 'ete study record'
    assert not dialog.cleanup_button.isEnabled()
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.text() == 'del'
    for command in CLEANUP_COMMANDS:
        editor.setText(command)
        assert dialog.cleanup_button.isEnabled()
        editor.setText(command + ' ')
        assert not dialog.cleanup_button.isEnabled()


@pytest.mark.parametrize('action', ['enter', 'escape', 'close', 'confirm'])
def test_cleanup_warning_defaults_to_cancel(window, repo, qtbot, action):
    seed(repo)
    before = repo.study_backup()
    dialog = SettingsDialog(window.shortcuts, window, clear_content=window.clear_content)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.cleanup_input.setText('remove all')
    dialog.scroll.ensureWidgetVisible(dialog.cleanup_button)

    def respond():
        warning = QApplication.activeModalWidget()
        try:
            assert isinstance(warning, DraftResetDialog)
            # A modal widget can be visible before Qt activates its window.
            qtbot.waitUntil(warning.isActiveWindow)
            assert warning.cancel_button.hasFocus() and warning.cancel_button.isDefault()
            assert '不可恢复' in warning.findChild(QLabel).text()
            if action == 'confirm':
                qtbot.mouseClick(warning.confirm_button, Qt.MouseButton.LeftButton)
            elif action == 'close':
                warning.close()
            else:
                qtbot.keyClick(warning, Qt.Key.Key_Return if action == 'enter' else Qt.Key.Key_Escape)
        finally:
            if warning is not None and warning.isVisible():
                warning.reject()

    QTimer.singleShot(0, respond)
    dialog.confirm_cleanup()
    if action == 'confirm':
        assert not repo.list_entries() and not repo.study_backup().records
        assert not dialog.cleanup_input.text()
    else:
        assert repo.study_backup() == before
        assert dialog.cleanup_input.text() == 'remove all'


@pytest.mark.parametrize('key', [Qt.Key.Key_Return, Qt.Key.Key_Enter])
@pytest.mark.parametrize('choice', ['confirm', 'cancel'])
def test_cleanup_input_enter_opens_warning_with_keyboard_choice(window, repo, qtbot, key, choice):
    seed(repo)
    before = repo.study_backup()
    dialog = SettingsDialog(window.shortcuts, window, clear_content=window.clear_content)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.scroll.ensureWidgetVisible(dialog.cleanup_input)
    dialog.cleanup_input.setText('remove all')
    dialog.cleanup_input.setFocus()
    responses, errors = [], []

    def respond():
        warning = QApplication.activeModalWidget()
        try:
            assert isinstance(warning, DraftResetDialog)
            qtbot.waitUntil(warning.isActiveWindow)
            assert warning.cancel_button.hasFocus() and warning.cancel_button.isDefault()
            qtbot.keyClick(warning.cancel_button, Qt.Key.Key_Tab)
            assert warning.confirm_button.hasFocus()
            qtbot.keyClick(warning.confirm_button, Qt.Key.Key_Tab)
            assert warning.cancel_button.hasFocus()
            qtbot.keyClick(warning.cancel_button, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
            assert warning.confirm_button.hasFocus()
            if choice == 'cancel':
                qtbot.keyClick(warning.confirm_button, Qt.Key.Key_Tab)
            qtbot.keyClick(QApplication.focusWidget(), key)
            responses.append(choice)
        except BaseException as error:
            errors.append(error)
        finally:
            if isinstance(warning, DraftResetDialog) and warning.isVisible():
                warning.reject()

    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.timeout.connect(respond)
    timer.start(0)
    qtbot.keyClick(dialog.cleanup_input, key)
    timer.stop()
    assert not errors, errors
    assert responses == [choice]
    assert dialog.isVisible()
    if choice == 'confirm':
        assert not repo.list_entries() and not dialog.cleanup_input.text()
    else:
        assert repo.study_backup() == before
        assert dialog.cleanup_input.text() == 'remove all'


def test_cleanup_input_enter_ignores_invalid_commands(window, qtbot, monkeypatch):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    monkeypatch.setattr(DraftResetDialog, 'exec', lambda self: pytest.fail('Invalid command opened warning'))
    for command in ('', 'del', 'Remove all', 'remove all '):
        dialog.cleanup_input.setText(command)
        qtbot.keyClick(dialog.cleanup_input, Qt.Key.Key_Return)
        assert dialog.cleanup_input.text() == command and dialog.isVisible()


def test_cleanup_resets_active_clock_and_deleted_edit_draft_without_redraw(window, repo):
    entries = seed(repo)
    window.study_page.load(AM)
    window.study_page.timer.start()
    window.reset_form(entries[0])
    window.clear_content('remove all')
    assert not window.study_page.timer.running
    assert window.study_page.timer.history is None
    assert window.study_page.timer.duration_seconds() == 0
    assert not window.study_page.tasks
    assert window.edit_id is None
    assert not window.model.rows
    assert repo.study_backup().study_last_add_new_vocabulary is None


def test_failed_cleanup_keeps_command_draft_clock_and_database(window, repo, qtbot, monkeypatch):
    entries = seed(repo)
    window.study_page.load(AM)
    window.study_page.timer.start()
    window.reset_form(entries[0])
    before = repo.study_backup()
    dialog = SettingsDialog(window.shortcuts, window, clear_content=window.clear_content)
    qtbot.addWidget(dialog)
    dialog.cleanup_input.setText('remove all')
    monkeypatch.setattr(DraftResetDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[-1]))

    def fail(command):
        raise sqlite3.OperationalError('disk error')

    monkeypatch.setattr(repo, 'clear_content', fail)
    dialog.confirm_cleanup()
    assert errors == ['disk error']
    assert dialog.cleanup_input.text() == 'remove all'
    assert window.study_page.timer.running
    assert window.edit_id == entries[0].id
    assert repo.study_backup() == before


def test_delete_records_keeps_vocabulary_and_draft_without_automatic_draw(window, repo):
    entries = seed(repo)
    window.study_page.load(AM)
    window.reset_form(entries[0])
    window.clear_content('delete study record')
    assert len(repo.list_entries()) == 3
    assert window.edit_id == entries[0].id
    assert not window.study_page.tasks
    assert repo.study_backup().study_last_add_new_vocabulary is None

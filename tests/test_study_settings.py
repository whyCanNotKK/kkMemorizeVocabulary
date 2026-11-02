from collections import Counter
from datetime import date, timedelta
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton

from vocabulary.domain import Sense, ValidationError, WordEntry
from vocabulary.learning import StudyContext, StudyDrawSettings
from vocabulary.shortcuts import SettingsDialog
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_study_backup, serialize_study_backup


AM = StudyContext(date(2026, 11, 2), False)


def seed(repo, count):
    for i in range(count):
        repo.save(WordEntry(f'word{i}', [Sense('n.', '释义')]))


def test_defaults_and_preferences_survive_restart_without_changing_other_settings(repo):
    assert repo.study_draw_settings() == StudyDrawSettings(10, 3)
    repo.write_shortcuts({'save': 'Alt+S'})
    repo.write_study_draw_settings(StudyDrawSettings(12, 4))
    reopened = Repository(repo.path)
    try:
        assert reopened.study_draw_settings() == StudyDrawSettings(12, 4)
        assert reopened.shortcuts()['save'] == 'Alt+S'
        assert reopened.study_backup().group_counter == 0
        assert reopened.connection.execute('PRAGMA user_version').fetchone()[0] == 5
    finally:
        reopened.close()


@pytest.mark.parametrize('value', [0, -1, 1001, True, 1.5, '10'])
@pytest.mark.parametrize('field', ['words_per_group', 'groups_per_day'])
def test_invalid_preferences_rejected(field, value):
    with pytest.raises(ValidationError):
        StudyDrawSettings(**{field: value})


def test_preferences_saved_atomically(repo):
    repo.write_study_draw_settings(StudyDrawSettings(5, 2))
    repo.connection.execute("""CREATE TRIGGER fail_study_settings BEFORE INSERT ON settings
        WHEN NEW.key='study17.groups_per_day' BEGIN SELECT RAISE(ABORT,'save failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='save failed'):
        repo.write_study_draw_settings(StudyDrawSettings(12, 4))
    assert repo.study_draw_settings() == StudyDrawSettings(5, 2)


@pytest.mark.parametrize('size,groups,count,expected', [
    (4, 2, 20, [4, 4]), (12, 2, 40, [12, 12]),
    (4, 3, 10, [4, 4, 2]), (1, 1, 3, [1]), (1000, 1000, 0, []),
])
def test_custom_draw_capacity_partial_groups_and_no_topup(repo, size, groups, count, expected):
    seed(repo, count)
    repo.write_study_draw_settings(StudyDrawSettings(size, groups))
    assert repo.prepare_study(StudyContext(AM.day, True)) == []
    tasks = repo.prepare_study(AM)
    assert list(Counter(task.group_number for task in tasks).values()) == expected
    before = repo.study_backup()
    assert before.group_counter == len(expected)
    assert before.study_last_add_new_vocabulary == AM.day.isoformat()
    repo.write_study_draw_settings(StudyDrawSettings(20, 5))
    assert repo.prepare_study(AM) == tasks
    assert repo.study_backup() == before


def test_changes_only_apply_to_future_batches_and_preserve_old_groups(repo):
    seed(repo, 50)
    repo.write_study_draw_settings(StudyDrawSettings(4, 2))
    repo.prepare_study(AM)
    before = repo.study_backup()
    repo.write_study_draw_settings(StudyDrawSettings(12, 2))
    tomorrow = StudyContext(AM.day + timedelta(days=1), False)
    tasks = repo.prepare_study(tomorrow)
    assert Counter(task.group_number for task in tasks) == {1: 4, 2: 4, 3: 12, 4: 12}
    after = repo.study_backup()
    assert all(after.records[name] == record for name, record in before.records.items())
    assert after.group_counter == 4


def test_custom_group_backup_roundtrip_keeps_local_draw_preferences(repo, tmp_path):
    seed(repo, 24)
    repo.write_study_draw_settings(StudyDrawSettings(12, 2))
    repo.prepare_study(AM)
    backup = parse_study_backup(serialize_study_backup(repo.study_backup()))
    target = Repository(tmp_path / 'target.sqlite3')
    try:
        target.write_study_draw_settings(StudyDrawSettings(2, 1))
        target.import_entries(backup.words)
        target.replace_study_progress(backup)
        assert target.study_backup().records == backup.records
        assert target.study_draw_settings() == StudyDrawSettings(2, 1)
        assert len(target.prepare_study(AM)) == 24  # no redraw or re-grouping
    finally:
        target.close()


def test_settings_section_order_save_and_reopen(window, repo, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    titles = {label.text(): label for label in dialog.findChildren(QLabel)}
    assert titles['十七天背单词设置'].y() < titles['快捷键设置'].y()
    assert (dialog.words_per_group.value(), dialog.groups_per_day.value()) == (10, 3)
    dialog.words_per_group.setValue(12)
    dialog.groups_per_day.setValue(4)
    assert repo.study_draw_settings() == StudyDrawSettings()
    done = next(button for button in dialog.findChildren(QPushButton) if button.text() == '完成')
    qtbot.mouseClick(done, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert repo.study_draw_settings() == StudyDrawSettings(12, 4)
    assert repo.study_backup().study_last_add_new_vocabulary is None
    again = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(again)
    assert (again.words_per_group.value(), again.groups_per_day.value()) == (12, 4)


def test_settings_cancel_and_shortcut_restore_leave_draw_preferences_unchanged(window, repo, qtbot):
    repo.write_study_draw_settings(StudyDrawSettings(12, 4))
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.words_per_group.setValue(5)
    dialog.restore()
    assert repo.study_draw_settings() == StudyDrawSettings(12, 4)
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert repo.study_draw_settings() == StudyDrawSettings(12, 4)


def test_settings_save_failure_preserves_dialog_input_and_database(window, repo, qtbot, monkeypatch):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.words_per_group.setValue(12)
    dialog.groups_per_day.setValue(4)

    def fail(settings):
        raise sqlite3.OperationalError('disk full')

    monkeypatch.setattr(repo, 'write_study_draw_settings', fail)
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda parent, title, message: errors.append(message))
    dialog.accept()
    assert dialog.isVisible()
    assert (dialog.words_per_group.value(), dialog.groups_per_day.value()) == (12, 4)
    assert 'disk full' in errors[0]
    assert repo.study_draw_settings() == StudyDrawSettings()


def test_settings_open_without_input_focus_and_background_click_clears_focus(window, qtbot):
    from PySide6.QtCore import QPoint

    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(dialog.hasFocus)
    title = next(label for label in dialog.findChildren(QLabel) if label.text() == '十七天背单词设置')
    for editor in (dialog.groups_per_day, dialog.words_per_group):
        qtbot.mouseClick(editor, Qt.MouseButton.LeftButton)
        assert editor.hasFocus()
        qtbot.mouseClick(title, Qt.MouseButton.LeftButton)
        assert not editor.hasFocus() and dialog.hasFocus()
        qtbot.mouseClick(editor, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(dialog, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        assert not editor.hasFocus() and dialog.hasFocus()
    qtbot.keyClick(dialog, Qt.Key.Key_Tab)
    assert dialog.groups_per_day.hasFocus()


def test_clicking_outside_shortcut_recording_cancels_without_stealing_focus(window, repo, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(dialog.hasFocus)
    dialog.record('add_related')
    assert dialog.editors['add_related'].hasFocus()
    qtbot.mouseClick(dialog.words_per_group, Qt.MouseButton.LeftButton)
    assert dialog._recording is None
    assert dialog.words_per_group.hasFocus()
    assert dialog.editors['add_related'].text() == window.shortcuts.labels()['add_related']
    assert repo.shortcuts()['add_related'] == 'Ctrl+N'

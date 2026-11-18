from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from vocabulary.domain import Sense, ValidationError, WordEntry
from vocabulary.learning import SpellingHideSettings, StudyContext, StudyDrawSettings
from vocabulary.shortcuts import SettingsDialog
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_study_backup, serialize_study_backup


AM = StudyContext(date(2026, 11, 2), False)


@pytest.mark.parametrize('legacy', [None, '0', '1'])
def test_spelling_hide_legacy_setting_maps_to_complete_configuration(repo, legacy):
    if legacy is not None:
        repo.connection.execute('INSERT INTO settings(key,value) VALUES (?,?)',
                                ('study17.hide_word_while_spelling', legacy))
        repo.connection.commit()
    assert repo.study_spelling_hide_settings() == SpellingHideSettings.from_enabled(legacy == '1')


def test_spelling_hide_child_changes_and_threshold_survive_reopen(window, repo, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    children = dialog.hide_condition_toggles
    assert dialog.hide_stage.currentData() == 15
    dialog.hide_word_toggle.click()
    assert repo.study_spelling_hide_settings().mode == 'all'
    assert all(toggle.isChecked() for toggle in children.values())
    children['by_important'].click()
    assert repo.study_spelling_hide_settings() == SpellingHideSettings('conditional', True, False, True)
    assert dialog.hide_word_toggle.property('checkState') == 1
    children['by_important'].click()
    assert repo.study_spelling_hide_settings() == SpellingHideSettings('conditional', True, True, True)
    dialog.hide_stage.setCurrentIndex(dialog.hide_stage.findData(7))
    assert repo.study_spelling_hide_settings() == SpellingHideSettings('conditional', True, True, True, 7)
    # Clicking partial always selects all, then clicking all clears every child.
    dialog.hide_word_toggle.click()
    assert repo.study_spelling_hide_settings().mode == 'all'
    dialog.hide_word_toggle.click()
    assert repo.study_spelling_hide_settings() == SpellingHideSettings(minimum_stage=7)
    children['by_stage'].click()
    assert repo.study_spelling_hide_settings().mode == 'conditional'
    children['by_stage'].click()
    assert repo.study_spelling_hide_settings().mode == 'off'
    children['by_learned'].click()
    reopened = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(reopened)
    assert reopened.hide_condition_toggles['by_learned'].isChecked()
    assert reopened.hide_stage.currentData() == 7
    assert reopened.hide_word_toggle.property('checkState') == 1


def test_spelling_hide_late_write_failure_rolls_back_entire_configuration(window, repo, qtbot):
    saved = SpellingHideSettings('conditional', True, False, True, 7)
    repo.set_study_spelling_hide_settings(saved)
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    repo.connection.execute("""CREATE TRIGGER fail_hide_threshold BEFORE INSERT ON settings
        WHEN NEW.key='study17.hide_word_while_spelling.minimum_stage'
        BEGIN SELECT RAISE(ABORT,'threshold failed'); END""")
    with qtbot.assertNotEmitted(dialog.spellingVisibilityChanged):
        dialog.hide_word_toggle.click()
    assert repo.study_spelling_hide_settings() == saved
    assert dialog.hide_word_toggle.property('checkState') == 1
    assert not dialog.hide_condition_toggles['by_important'].isChecked()
    with qtbot.assertNotEmitted(dialog.spellingVisibilityChanged):
        dialog.hide_stage.setCurrentIndex(dialog.hide_stage.findData(15))
    assert repo.study_spelling_hide_settings() == saved
    assert dialog.hide_stage.currentData() == 7


def seed(repo, count):
    for i in range(count):
        repo.save(WordEntry(f'word{i}', [Sense('n.', '释义')]))


def test_defaults_and_preferences_survive_restart_without_changing_other_settings(repo):
    assert not repo.study_hide_word_while_spelling()
    repo.set_study_hide_word_while_spelling(True)
    assert repo.study_draw_settings() == StudyDrawSettings(10, 3)
    repo.write_shortcuts({'save': 'Alt+S'})
    repo.write_study_draw_settings(StudyDrawSettings(12, 4))
    reopened = Repository(repo.path)
    try:
        assert reopened.study_draw_settings() == StudyDrawSettings(12, 4)
        assert reopened.shortcuts()['save'] == 'Alt+S'
        assert reopened.study_hide_word_while_spelling()
        assert reopened.study_backup().group_counter == 0
        assert reopened.connection.execute('PRAGMA user_version').fetchone()[0] == 10
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
    assert before.study_last_add_new_vocabulary == (AM.day.isoformat() if count else None)
    repo.write_study_draw_settings(StudyDrawSettings(20, 5))
    assert repo.prepare_study(AM) == tasks
    assert repo.study_backup() == replace(before, words_per_group=20, groups_per_day=5)


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


def test_custom_group_backup_roundtrip_restores_draw_preferences(repo, tmp_path):
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
        assert target.study_draw_settings() == StudyDrawSettings(12, 2)
        assert len(target.prepare_study(AM)) == 24  # no redraw or re-grouping
    finally:
        target.close()


def test_hide_word_setting_saves_reopens_and_rolls_back_on_failure(window, repo, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.hide_word_toggle.isChecked()
    assert dialog.hide_word_toggle.mapTo(dialog, dialog.hide_word_toggle.rect().center()).y() > (
        dialog.new_words_toggle.mapTo(dialog, dialog.new_words_toggle.rect().center()).y())
    assert dialog.hide_word_toggle.x() == dialog.new_words_toggle.x()
    with qtbot.waitSignal(dialog.spellingVisibilityChanged) as changed:
        qtbot.mouseClick(dialog.hide_word_toggle, Qt.MouseButton.LeftButton)
    assert changed.args == [SpellingHideSettings.from_enabled(True)]
    assert repo.study_hide_word_while_spelling()
    dialog.close()
    again = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(again)
    again.show()
    assert again.hide_word_toggle.isChecked()
    repo.connection.execute("""CREATE TRIGGER fail_hide_setting BEFORE INSERT ON settings
        WHEN NEW.key='study17.hide_word_while_spelling'
        BEGIN SELECT RAISE(ABORT,'save failed'); END""")
    with qtbot.assertNotEmitted(again.spellingVisibilityChanged):
        qtbot.mouseClick(again.hide_word_toggle, Qt.MouseButton.LeftButton)
    assert repo.study_hide_word_while_spelling()
    assert again.hide_word_toggle.isChecked()
    assert 'save failed' in again.toast.label.text()


def test_settings_opened_by_window_updates_practice_mode(window, repo, monkeypatch):
    def interact(dialog):
        dialog.hide_word_toggle.click()
        assert window.study_page.table.hide_word_while_spelling
        return 0
    monkeypatch.setattr(SettingsDialog, 'exec', interact)
    window.open_settings()
    assert repo.study_hide_word_while_spelling()


def test_settings_section_order_save_and_reopen(window, repo, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    titles = {label.text(): label for label in dialog.findChildren(QLabel)}
    assert titles['十七天背单词设置'].y() < titles['快捷键设置'].y()
    assert (dialog.words_per_group.value(), dialog.groups_per_day.value()) == (10, 3)
    dialog.words_per_group.setValue(12)
    assert repo.study_draw_settings() == StudyDrawSettings(12, 3)
    dialog.groups_per_day.setValue(4)
    assert not any(button.text() == '完成' for button in dialog.findChildren(QPushButton))
    assert repo.study_draw_settings() == StudyDrawSettings(12, 4)
    assert dialog.isVisible()
    dialog.close()
    reopened = Repository(repo.path)
    try:
        assert reopened.study_draw_settings() == StudyDrawSettings(12, 4)
    finally:
        reopened.close()
    assert repo.study_backup().study_last_add_new_vocabulary is None
    again = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(again)
    assert (again.words_per_group.value(), again.groups_per_day.value()) == (12, 4)


def test_settings_close_and_shortcut_restore_keep_immediate_draw_changes(window, repo, qtbot):
    repo.write_study_draw_settings(StudyDrawSettings(12, 4))
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.words_per_group.setValue(5)
    dialog.restore()
    assert repo.study_draw_settings() == StudyDrawSettings(5, 4)
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert repo.study_draw_settings() == StudyDrawSettings(5, 4)


def test_settings_save_failure_restores_last_persisted_values(window, repo, qtbot, monkeypatch):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.words_per_group.setValue(12)

    def fail(settings):
        raise sqlite3.OperationalError('disk full')

    monkeypatch.setattr(repo, 'write_study_draw_settings', fail)
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda parent, title, message: errors.append(message))
    dialog.groups_per_day.setValue(4)
    assert dialog.isVisible()
    assert (dialog.words_per_group.value(), dialog.groups_per_day.value()) == (12, 3)
    assert 'disk full' in errors[0]
    assert repo.study_draw_settings() == StudyDrawSettings(12, 3)
    monkeypatch.undo()
    dialog.groups_per_day.setValue(5)
    assert repo.study_draw_settings() == StudyDrawSettings(12, 5)


def test_typing_valid_study_setting_saves_without_leaving_input(window, repo, qtbot):
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(dialog.hasFocus)
    dialog.words_per_group.setFocus()
    qtbot.waitUntil(dialog.words_per_group.hasFocus)
    editor = dialog.words_per_group.lineEdit()
    editor.selectAll()
    qtbot.keyClicks(editor, '24')
    assert dialog.words_per_group.hasFocus()
    assert repo.study_draw_settings() == StudyDrawSettings(24, 3)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert dialog.isVisible()
    assert dialog._recording is None


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

from datetime import date, timedelta
import sqlite3

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.shortcuts import SettingsDialog
from vocabulary.storage import Repository


AM = StudyContext(date(2026, 11, 12), False)
PM = StudyContext(AM.day, True)


def add(repo, text='new'):
    return repo.save(WordEntry(text, [Sense('n.', '释义')]))


def test_empty_draw_does_not_consume_date_and_can_retry(repo):
    yesterday = AM.day - timedelta(days=1)
    first = add(repo, 'first')
    repo.prepare_study(StudyContext(yesterday, False))
    before = repo.study_backup()
    repo.prepare_study(AM)
    assert repo.study_backup() == before
    added = add(repo)
    tasks = repo.prepare_study(AM)
    assert {task.entry.id for task in tasks} == {first.id, added.id}
    assert repo.study_backup().study_last_add_new_vocabulary == AM.day.isoformat()


def test_startup_disables_without_candidates_and_preserves_manual_choice(repo):
    assert repo.study_new_words_enabled()  # legacy missing key defaults to on
    repo.initialize_study_new_words()
    assert not repo.study_new_words_enabled()
    assert repo.study_backup().study_last_add_new_vocabulary is None
    add(repo)
    reopened = Repository(repo.path)
    try:
        reopened.initialize_study_new_words()
        assert not reopened.study_new_words_enabled()
        assert reopened.prepare_study(AM) == []
        reopened.set_study_new_words_enabled(True, AM)
        assert len(reopened.study_tasks(AM)) == 1
        # Opening another repository/worker must not run the startup policy.
        worker = Repository(repo.path)
        try:
            assert worker.study_new_words_enabled()
        finally:
            worker.close()
        reopened.initialize_study_new_words()
        assert not reopened.study_new_words_enabled()
    finally:
        reopened.close()


def test_startup_keeps_legacy_enabled_when_candidates_exist(repo):
    add(repo)
    repo.initialize_study_new_words()
    assert repo.study_new_words_enabled()
    assert repo.connection.execute(
        "SELECT value FROM settings WHERE key='study17.new_words_enabled'").fetchone()[0] == '1'


def test_candidate_check_excludes_learned_participants_finished_and_related(repo):
    word = repo.save(WordEntry('participant', [Sense('n.', '')],
                              [RelatedWord('related', [Sense('n.', '')])]))
    repo.prepare_study(AM)
    repo.connection.execute('UPDATE memorize_vocabulary_17day SET stage=0,last_completed_on=? WHERE word_id=?',
                            (AM.day.isoformat(), word.id))
    repo.connection.commit()
    repo.save(WordEntry('learned', [Sense('n.', '')], learned=True))
    assert not repo.has_new_study_words()
    repo.initialize_study_new_words()
    with pytest.raises(ValidationError, match='当前待记单词列表中的词均已背过'):
        repo.set_study_new_words_enabled(True, AM)
    assert not repo.study_new_words_enabled()
    add(repo)
    assert repo.has_new_study_words()


def test_disabled_does_not_select_candidates_or_write_date(repo):
    add(repo)
    repo.set_study_new_words_enabled(False, AM)
    statements = []
    repo.connection.set_trace_callback(statements.append)
    try:
        assert repo.prepare_study(AM) == []
    finally:
        repo.connection.set_trace_callback(None)
    assert not any('random()' in statement for statement in statements)
    assert repo.study_backup().study_last_add_new_vocabulary is None
    assert repo.study_backup().group_counter == 0


def test_enable_respects_afternoon_and_existing_daily_batch(repo):
    add(repo)
    repo.set_study_new_words_enabled(False, AM)
    repo.set_study_new_words_enabled(True, PM)
    assert repo.study_new_words_enabled()
    assert not repo.has_study_progress()
    assert repo.study_backup().study_last_add_new_vocabulary is None
    repo.prepare_study(AM)
    before = repo.study_backup()
    add(repo, 'later')
    repo.set_study_new_words_enabled(False, AM)
    repo.set_study_new_words_enabled(True, AM)
    assert repo.study_backup() == before
    repo.clear_content('delete study record')
    assert repo.study_new_words_enabled()  # cleanup retains the local preference


@pytest.mark.parametrize('failure', ['date', 'preference'])
def test_enable_and_draw_failures_rollback_everything(repo, failure):
    add(repo)
    repo.set_study_new_words_enabled(False, AM)
    key = 'study_last_add_new_vocabulary' if failure == 'date' else 'study17.new_words_enabled'
    repo.connection.execute(f"""CREATE TRIGGER fail_enable BEFORE INSERT ON settings
        WHEN NEW.key='{key}' BEGIN SELECT RAISE(ABORT,'save failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='save failed'):
        repo.set_study_new_words_enabled(True, AM)
    assert not repo.study_new_words_enabled()
    assert not repo.has_study_progress()
    assert repo.study_backup().study_last_add_new_vocabulary is None
    assert repo.study_backup().group_counter == 0


@pytest.mark.parametrize('completed', [False, True])
def test_enable_adds_to_existing_review_task_and_can_finish(repo, completed):
    review = add(repo, 'review')
    yesterday = StudyContext(AM.day - timedelta(days=1), False)
    repo.prepare_study(yesterday)
    repo.complete_study(review.id, yesterday)
    repo.complete_study(review.id, StudyContext(yesterday.day, True))
    repo.set_study_new_words_enabled(False, AM)
    history = repo.start_study_task(AM)
    repo.save_study_duration(AM, 12)
    if completed:
        repo.complete_study(review.id, AM)
    added = add(repo)
    repo.set_study_new_words_enabled(True, AM)
    updated = repo.study_task_history(AM)
    assert updated.id == history.id and updated.started_at == history.started_at
    assert updated.duration_seconds == 12
    assert updated.review_total == updated.new_total == 1
    assert updated.review_done == int(completed) and updated.new_done == 0
    assert updated.status == 0 and updated.ended_at is None
    if not completed:
        repo.complete_study(review.id, AM)
    repo.complete_study(added.id, AM)
    assert repo.study_task_history(AM).status == 1


def test_user_flow_enable_from_settings_refreshes_page_and_toast(window, repo, qtbot, monkeypatch):
    repo.initialize_study_new_words()
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    dialog = SettingsDialog(window.shortcuts, window, set_study_new_words=window.set_study_new_words)
    qtbot.addWidget(dialog)
    dialog.show()
    toggle = dialog.new_words_toggle
    assert not toggle.isChecked()
    assert toggle.toolTip() == '点击开启每日新背单词'
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert not toggle.isChecked() and not repo.study_new_words_enabled()
    assert dialog.toast.label.text() == '当前待记单词列表中的词均已背过'
    assert dialog.toast.isVisible()
    qtbot.wait(200)
    assert dialog.toast.geometry().bottom() < toggle.mapTo(dialog, toggle.rect().topLeft()).y()
    dialog.close()
    # Importing words also leaves the preference off until the user opts in.
    repo.import_entries([WordEntry('imported', [Sense('n.', '导入词')])])
    window.set_filter('pending')
    window.set_filter('study17')
    assert not window.study_page.tasks
    again = SettingsDialog(window.shortcuts, window, set_study_new_words=window.set_study_new_words)
    qtbot.addWidget(again)
    again.show()
    qtbot.mouseClick(again.new_words_toggle, Qt.MouseButton.LeftButton)
    assert again.new_words_toggle.isChecked()
    assert again.new_words_toggle.toolTip() == '点击关闭每日新背单词'
    assert [task.entry.text for task in window.study_page.tasks] == ['imported']
    assert window.study_page.new_count.accessibleName() == '新词：剩余1/1'
    qtbot.mouseClick(again.new_words_toggle, Qt.MouseButton.LeftButton)
    assert not repo.study_new_words_enabled()
    assert len(window.study_page.tasks) == 1  # existing tasks remain


def test_settings_save_failure_restores_toggle(window, repo, qtbot):
    repo.initialize_study_new_words()
    add(repo)
    repo.connection.execute("""CREATE TRIGGER fail_toggle BEFORE INSERT ON settings
        WHEN NEW.key='study17.new_words_enabled' BEGIN SELECT RAISE(ABORT,'save failed'); END""")
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.new_words_toggle, Qt.MouseButton.LeftButton)
    assert not dialog.new_words_toggle.isChecked()
    assert not repo.study_new_words_enabled()
    assert 'save failed' in dialog.toast.label.text()


def test_enable_after_finished_review_reopens_visible_task(window, repo, monkeypatch):
    review = add(repo, 'review')
    yesterday = StudyContext(AM.day - timedelta(days=1), False)
    repo.prepare_study(yesterday)
    repo.complete_study(review.id, yesterday)
    repo.complete_study(review.id, StudyContext(yesterday.day, True))
    repo.set_study_new_words_enabled(False, AM)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    page.complete(review.id, 'study')
    assert page.completion_notice.isVisible()
    assert page.timer_button.isHidden()
    added = add(repo)
    window.set_study_new_words(True, AM)
    assert not page.completion_notice.isVisible()
    assert page.timer_button.isVisible() and page.timer_button.isEnabled()
    assert page.new_count.accessibleName() == '新词：剩余1/1'
    assert page.review_count.accessibleName() == '复习：剩余0/1'
    page.complete(added.id, 'study')
    assert page.completion_notice.isVisible()
    assert repo.study_task_history(AM).new_done == 1

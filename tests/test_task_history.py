from dataclasses import asdict
from datetime import date, datetime, timedelta
import json
import sqlite3

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from vocabulary.domain import Sense, ValidationError, WordEntry
from vocabulary.learning import BEIJING, StudyContext
from vocabulary.storage import Repository
from vocabulary.study_timer import StudyTimer
from vocabulary.study_transfer import parse_study_backup, serialize_study_backup


DAY = date(2026, 11, 5)
AM = StudyContext(DAY, False)
PM = StudyContext(DAY, True)
START = datetime(2026, 11, 5, 14, 59, 59, tzinfo=BEIJING)


def seed(repo, count=3):
    words = [repo.save(WordEntry(f'word{i}', [Sense('n.', '释义')])) for i in range(count)]
    repo.prepare_study(AM)
    return words


def test_start_is_lazy_unique_and_initializes_remaining_categories(repo):
    assert repo.start_study_task(AM) is None
    words = seed(repo)
    with repo.connection:
        repo.connection.execute('UPDATE memorize_vocabulary_17day SET morning_done=1 WHERE word_id=?', (words[0].id,))
        repo.connection.execute('UPDATE memorize_vocabulary_17day SET stage=4,last_completed_on=? WHERE word_id=?',
                                ((DAY - timedelta(days=2)).isoformat(), words[1].id))
    assert repo.study_task_history(AM) is None
    record = repo.start_study_task(AM, instant=START)
    assert (record.new_total, record.review_total, record.new_done, record.review_done) == (1, 1, 0, 0)
    assert record.started_at == '2026-11-05T14:59:59+08:00'
    assert record.ended_at is None and record.duration_seconds == record.status == 0
    assert repo.start_study_task(AM, instant=START + timedelta(days=1)) == record
    reopened = Repository(repo.path)
    try:
        assert reopened.start_study_task(AM) == record
    finally:
        reopened.close()


def test_completion_counts_before_stage_advance_and_only_last_word_finishes(repo):
    words = seed(repo, 2)
    for word in words:
        repo.complete_study(word.id, AM, instant=START)
    assert repo.study_task_history(AM).status == 1
    assert repo.complete_study(words[0].id, PM, duration_seconds=1234, instant=START)
    record = repo.study_task_history(PM)
    assert (record.new_done, record.status, record.ended_at) == (1, 0, None)
    assert not repo.complete_study(words[0].id, PM, duration_seconds=99999)
    assert repo.study_task_history(PM) == record
    repo.complete_study(words[1].id, PM, duration_seconds=2345, instant=START + timedelta(days=1))
    record = repo.study_task_history(PM)
    assert record.status == 1 and record.new_done == record.new_total == 2
    assert record.duration_seconds == 2345 and record.ended_at == '2026-11-06T14:59:59+08:00'


@pytest.mark.parametrize('operation', ['insert', 'update'])
def test_history_failure_rolls_back_word_progress_and_counter(repo, operation):
    word = seed(repo, 1)[0]
    if operation == 'update':
        repo.start_study_task(AM)
    before = repo.study_backup()
    repo.connection.execute(f'''CREATE TRIGGER fail_history BEFORE {operation.upper()} ON study_task_history
        BEGIN SELECT RAISE(ABORT,'history failed'); END''')
    with pytest.raises(sqlite3.IntegrityError):
        repo.complete_study(word.id, AM)
    assert repo.study_backup() == before


def test_delete_adjusts_current_task_only_and_does_not_complete_empty_task(repo):
    words = seed(repo, 2)
    repo.start_study_task(AM)
    repo.start_study_task(PM)
    repo.complete_study(words[0].id, AM)
    repo.complete_study(words[0].id, PM)  # stage=2, but this task's contribution was new.
    morning = repo.study_task_history(AM)
    repo.delete(words[0].id, PM)
    assert repo.study_task_history(AM) == morning
    record = repo.study_task_history(PM)
    assert (record.new_total, record.new_done) == (1, 0)
    repo.delete(words[1].id, PM)
    record = repo.study_task_history(PM)
    assert record.new_total == record.new_done == 0
    assert record.status == 0 and record.ended_at is None


def test_review_delete_and_delete_failure_are_atomic(repo):
    word = seed(repo, 1)[0]
    with repo.connection:
        repo.connection.execute('UPDATE memorize_vocabulary_17day SET stage=15,last_completed_on=?',
                                ((DAY - timedelta(days=8)).isoformat(),))
    repo.complete_study(word.id, AM)
    before = repo.study_task_history(AM)
    repo.connection.execute('''CREATE TRIGGER fail_delete BEFORE DELETE ON words
        BEGIN SELECT RAISE(ABORT,'delete failed'); END''')
    with pytest.raises(sqlite3.IntegrityError):
        repo.delete(word.id, AM)
    assert repo.study_task_history(AM) == before
    repo.connection.execute('DROP TRIGGER fail_delete')
    repo.delete(word.id, AM)
    after = repo.study_task_history(AM)
    assert after.review_total == after.review_done == 0
    assert after.status == 1 and after.ended_at == before.ended_at


@pytest.fixture
def clock_timer(qapp, repo):
    now = [0]
    timer = StudyTimer(repo, clock=lambda: now[0])
    timer.bind(AM)
    yield now, timer
    timer.dispose()


def test_timer_rounds_seconds_excludes_pauses_and_uses_cumulative_writes(repo, clock_timer):
    seed(repo)
    now, timer = clock_timer
    assert timer.start()
    now[0] = 1_234_000_000
    timer.checkpoint()
    timer.checkpoint()
    assert timer.running and repo.study_task_history(AM).duration_seconds == 1
    now[0] = 2_345_000_000
    timer.pause()
    assert repo.study_task_history(AM).duration_seconds == 2
    now[0] += 100_000_000_000
    timer.start()
    now[0] += 756_000_000
    timer.pause()
    assert repo.study_task_history(AM).duration_seconds == 3
    timer.bind(AM)
    assert not timer.running


def test_idle_counts_to_exact_boundary_and_app_events_reset_it(repo, clock_timer, qapp, window):
    seed(repo)
    now, timer = clock_timer
    timer.start()
    now[0] = 59_000_000_000
    qapp.sendEvent(window.search_box, QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(2, 2), QPointF(2, 2),
                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    now[0] = 118_000_000_000
    timer.check_idle()
    assert timer.running  # Activity elsewhere in the app counts.
    qapp.sendEvent(window, QMouseEvent(QEvent.Type.MouseMove, QPointF(2, 2), QPointF(2, 2),
                    Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    now[0] = 125_000_000_000
    timer.check_idle()
    assert not timer.running
    assert repo.study_task_history(AM).duration_seconds == 119


def test_failed_pause_freezes_and_retries_without_counting_wait_time(repo, clock_timer, monkeypatch):
    seed(repo)
    now, timer = clock_timer
    timer.start()
    now[0] = 1_500_000_000
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'save_study_duration', lambda *args: (_ for _ in ()).throw(OSError('locked')))
        with pytest.raises(OSError):
            timer.pause()
    assert not timer.running and timer.duration_seconds() == 2
    now[0] += 100_000_000_000
    timer.start()
    assert repo.study_task_history(AM).duration_seconds == 2
    now[0] += 600_000_000
    timer.suspend()
    assert repo.study_task_history(AM).duration_seconds == 3


def test_page_reveal_starts_pause_resume_and_same_context_reload_keeps_running(window, repo, monkeypatch):
    words = seed(repo, 2)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    now = [0]
    page.timer.clock = lambda: now[0]
    assert repo.study_task_history(AM) is None
    assert page.timer_button.toolTip() == '开始背单词'
    page.reveal(words[0].id, True)
    assert page.timer.running and page.timer_button.toolTip() == '暂停'
    now[0] += 1_234_000_000
    page.toggle_timer()
    assert page.timer_button.toolTip() == '继续'
    page.reveal(words[1].id, True)
    assert page.timer.running
    window.set_filter('pending')
    assert page.timer.running
    window.set_filter('study17')
    assert page.timer.running
    now[0] += 1_765_000_000
    page.complete(words[0].id, 'study')
    page.complete(words[1].id, 'study')
    assert repo.study_task_history(AM).duration_seconds == 3
    assert not page.timer.running and not page.timer_button.isEnabled()


def test_failed_first_mark_does_not_start_or_create_history(window, repo, monkeypatch):
    word = seed(repo, 1)[0]
    window.study_page.load(AM)
    repo.connection.execute('''CREATE TRIGGER fail_history BEFORE UPDATE ON study_task_history
        BEGIN SELECT RAISE(ABORT,'failed'); END''')
    window.study_page.complete(word.id, 'study')
    assert repo.study_task_history(AM) is None
    assert not window.study_page.timer.running and len(window.study_page.tasks) == 1


def test_context_change_pauses_old_and_reopen_loads_paused(repo, clock_timer):
    seed(repo)
    now, timer = clock_timer
    timer.start()
    now[0] = 1_234_000_000
    timer.bind(PM)
    assert not timer.running and repo.study_task_history(AM).duration_seconds == 1
    assert repo.study_task_history(PM) is None
    timer.bind(AM)
    assert timer.history is not None and not timer.running


def test_json_history_roundtrip_restore_ids_empty_overwrite_and_failure_rollback(repo, tmp_path):
    seed(repo, 1)
    repo.start_study_task(AM, instant=START)
    repo.save_study_duration(AM, 1234)
    backup = parse_study_backup(serialize_study_backup(repo.study_backup()))
    assert backup.task_history == [asdict(repo.study_task_history(AM))]
    target = Repository(tmp_path / 'target.sqlite3')
    try:
        target.import_entries(backup.words)
        target.replace_study_progress(backup)
        assert target.study_backup().task_history == backup.task_history
        before = target.study_backup()
        target.connection.execute('''CREATE TRIGGER fail_history BEFORE INSERT ON study_task_history
            BEGIN SELECT RAISE(ABORT,'failed'); END''')
        with pytest.raises(sqlite3.IntegrityError):
            target.replace_study_progress(backup)
        assert target.study_backup() == before
        target.connection.execute('DROP TRIGGER fail_history')
        backup.task_history = []
        target.replace_study_progress(backup)
        assert target.study_backup().task_history == []
    finally:
        target.close()


@pytest.mark.parametrize('change', [
    lambda d: d.update(version=3),
    lambda d: d.pop('task_history'),
    lambda d: d.update(task_history={}),
    lambda d: d['task_history'].append(d['task_history'][0].copy()),
    lambda d: d['task_history'][0].update(duration_seconds=True),
    lambda d: d['task_history'][0].update(new_done=999),
    lambda d: d['task_history'][0].update(status=1),
    lambda d: d['task_history'][0].update(started_at='2026-11-05T08:00:00'),
    lambda d: d['task_history'][0].update(ended_at='2026-11-05T08:00:00+08:00'),
])
def test_json_history_invalid_rejected(repo, change):
    seed(repo)
    repo.start_study_task(AM)
    document = json.loads(serialize_study_backup(repo.study_backup()))
    change(document)
    with pytest.raises(ValidationError):
        parse_study_backup(json.dumps(document))


def test_close_cancel_keeps_running_success_saves_rounded_seconds(window, repo, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    seed(repo)
    page = window.study_page
    page.load(AM)
    ticks = [0]
    page.timer.clock = lambda: ticks[0]
    page.toggle_timer()
    ticks[0] = 1_234_000_000
    with monkeypatch.context() as patch:
        patch.setattr(window, 'resolve_dirty', lambda: False)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted() and page.timer.running
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() and not page.timer.running
    assert repo.study_task_history(AM).duration_seconds == 1
    assert repo.study_task_history(AM).ended_at is None


def test_native_suspend_and_resume_never_count_sleep(repo, clock_timer):
    import ctypes
    from ctypes import wintypes
    from vocabulary.study_timer import PowerEvents
    seed(repo)
    ticks, timer = clock_timer
    timer.start()
    ticks[0] = 1_500_000_000
    events = PowerEvents(timer)
    message = wintypes.MSG()
    message.message, message.wParam = 0x0218, 4
    events.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(message))
    ticks[0] += 3600_000_000_000
    message.wParam = 0x12
    events.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(message))
    assert not timer.running and repo.study_task_history(AM).duration_seconds == 2


def test_export_uses_saved_duration_and_full_import_loads_paused(window, repo, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    seed(repo, 2)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    ticks = [0]
    page.timer.clock = lambda: ticks[0]
    page.toggle_timer()
    ticks[0] = 1_234_000_000
    path = tmp_path / 'export.json'
    errors = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[-1]))
    window.export_study_progress()
    assert not errors and page.timer.running
    backup = parse_study_backup(path.read_text(encoding='utf-8'))
    assert backup.task_history[0]['duration_seconds'] == 0
    backup.task_history[0]['duration_seconds'] = 4321
    ticks[0] += 456_000_000
    monkeypatch.setattr(window, 'choose_study_import_mode', lambda: 'full')
    window.import_study_progress(backup, require_choice=True)
    assert not errors and not page.timer.running
    assert page.timer_button.toolTip() == '继续'
    assert repo.study_backup().task_history == backup.task_history
    page.toggle_timer()
    ticks[0] += 1_500_000_000
    page.toggle_timer()
    assert repo.study_task_history(AM).duration_seconds == 4323


def test_v5_upgrade_does_not_invent_history_and_failed_v6_leaves_v5_intact(tmp_path, monkeypatch):
    from vocabulary import migration
    path = tmp_path / 'v5.sqlite3'
    raw = sqlite3.connect(path)
    for name in migration.MIGRATIONS[:5]:
        raw.executescript(migration.read_migration(name))
    raw.execute("INSERT INTO words(text,normalized_text,sort_order) VALUES ('old','old',1)")
    raw.execute('INSERT INTO memorize_vocabulary_17day(word_id,group_number) VALUES (1,1)')
    raw.execute('PRAGMA user_version=5')
    raw.commit()
    raw.close()
    read = migration.read_migration
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'read_migration', lambda name: read(name) + '\nSELECT * FROM missing_table;')
        with pytest.raises(sqlite3.OperationalError):
            Repository(path)
    raw = sqlite3.connect(path)
    assert raw.execute('PRAGMA user_version').fetchone()[0] == 5
    assert raw.execute('SELECT stage FROM memorize_vocabulary_17day').fetchone()[0] == 1
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='study_task_history'").fetchone() is None
    raw.close()
    upgraded = Repository(path)
    try:
        assert upgraded.study_task_histories() == []
        assert upgraded.connection.execute('PRAGMA user_version').fetchone()[0] == 7
    finally:
        upgraded.close()


def test_consumed_native_shortcuts_also_reset_application_idle(window, repo, monkeypatch):
    seed(repo)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    timer = window.study_page.timer
    ticks = [0]
    timer.clock = lambda: ticks[0]
    timer.start()
    ticks[0] = 59_000_000_000
    window.shortcuts.callbacks['tab_pending']()
    assert timer.running
    ticks[0] = 118_000_000_000
    timer.check_idle()
    assert timer.running
    ticks[0] = 119_000_000_000
    timer.check_idle()
    assert not timer.running


def test_deleting_last_pending_word_pauses_without_finishing(window, repo):
    word = seed(repo, 1)[0]
    page = window.study_page
    page.load(AM)
    ticks = [0]
    page.timer.clock = lambda: ticks[0]
    page.toggle_timer()
    ticks[0] = 1_234_000_000
    repo.delete(word.id, AM)
    assert page.load(AM)
    record = repo.study_task_history(AM)
    assert record.status == 0 and record.ended_at is None and record.duration_seconds == 1
    assert not page.timer.running and not page.timer_button.isEnabled()

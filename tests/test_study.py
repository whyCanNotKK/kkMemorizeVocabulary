from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository


DAY = date(2026, 10, 30)
AM = StudyContext(DAY, False)
PM = StudyContext(DAY, True)


def add(repo, count=1, prefix="word", learned=False):
    return [repo.save(WordEntry(f"{prefix}{i}", [Sense("n.", "释义")],
                               [RelatedWord("related", [Sense("v.", "关联释义")])], learned=learned))
            for i in range(count)]


def progress(repo, word_id):
    return dict(repo.connection.execute(
        "SELECT * FROM memorize_vocabulary_17day WHERE word_id=?", (word_id,)).fetchone())


def test_beijing_period_and_date():
    assert StudyContext.at(datetime(2026, 10, 30, 6, 59, tzinfo=timezone.utc)) == AM
    assert StudyContext.at(datetime(2026, 10, 30, 7, tzinfo=timezone.utc)) == PM
    assert StudyContext.at(datetime(2026, 10, 30, 16, tzinfo=timezone.utc)) == StudyContext(
        date(2026, 10, 31), False)
    with pytest.raises(ValueError):
        StudyContext.at(datetime(2026, 10, 30))


@pytest.mark.parametrize("count", [0, 1, 9, 10, 11, 29, 30, 45])
def test_daily_batch_fixed_short_or_empty_and_restart(repo, count):
    add(repo, count)
    add(repo, 2, prefix="learned", learned=True)
    tasks = repo.prepare_study(AM)
    assert len(tasks) == min(count, 30)
    ids = [task.entry.id for task in tasks]
    assert len(set(ids)) == len(ids)
    assert [task.group_number for task in tasks] == [i // 10 + 1 for i in range(len(ids))]
    add(repo, 2, prefix="later")
    reopened = Repository(repo.path)
    try:
        assert [task.entry.id for task in reopened.prepare_study(AM)] == ids
        assert [task.entry.id for task in reopened.prepare_study(PM)] == ids
    finally:
        reopened.close()


def test_afternoon_never_generates(repo):
    add(repo, 40)
    assert repo.prepare_study(PM) == []
    assert repo.connection.execute("SELECT count(*) FROM settings WHERE key LIKE 'study17.%'").fetchone()[0] == 0


def test_normal_schedule_finishes_and_never_reenrolls(repo):
    entry = add(repo)[0]
    repo.prepare_study(AM)
    for offset, next_day, next_stage in [(0, 1, 2), (1, 3, 4), (3, 6, 7), (6, 14, 15), (14, None, 15)]:
        day = DAY + timedelta(days=offset)
        morning, afternoon = StudyContext(day, False), StudyContext(day, True)
        assert [task.entry.id for task in repo.study_tasks(morning)] == [entry.id]
        repo.complete_study(entry.id, morning)
        assert repo.study_tasks(morning) == []
        assert len(repo.study_tasks(afternoon)) == 1
        repo.complete_study(entry.id, afternoon)
        state = progress(repo, entry.id)
        assert state['stage'] == next_stage
        assert state['last_completed_on'] == day.isoformat()
        assert state['morning_done'] == state['afternoon_done'] == 0
        assert bool(state['completed']) == (next_day is None)
        if next_day is not None:
            assert state['next_due_on'] == (DAY + timedelta(days=next_day)).isoformat()
        repo.complete_study(entry.id, afternoon)
        assert progress(repo, entry.id) == state
        assert repo.study_tasks(afternoon) == []
    assert repo.prepare_study(StudyContext(DAY + timedelta(days=99), False)) == []
    assert not repo.get(entry.id).learned


def test_afternoon_before_morning_and_cross_day_preserves_flags(repo):
    entry = add(repo)[0]
    repo.prepare_study(AM)
    repo.complete_study(entry.id, PM)
    state = progress(repo, entry.id)
    assert state['afternoon_done'] and not state['morning_done']
    assert state['last_completed_on'] is None and state['stage'] == 1
    assert repo.study_tasks(PM) == []
    later = DAY + timedelta(days=5)
    assert len(repo.study_tasks(StudyContext(later, False))) == 1
    repo.complete_study(entry.id, StudyContext(later, False))
    assert progress(repo, entry.id)['afternoon_done'] == 0
    assert len(repo.study_tasks(StudyContext(later, True))) == 1
    repo.complete_study(entry.id, StudyContext(later, True))
    assert progress(repo, entry.id)['next_due_on'] == (later + timedelta(days=1)).isoformat()


def test_missed_afternoon_and_overdue_intervals(repo):
    entry = add(repo)[0]
    repo.prepare_study(AM)
    repo.complete_study(entry.id, AM)
    overdue = DAY + timedelta(days=6)
    assert repo.study_tasks(StudyContext(overdue, False)) == []
    repo.complete_study(entry.id, StudyContext(overdue, True))
    assert progress(repo, entry.id)['next_due_on'] == (overdue + timedelta(days=1)).isoformat()
    later = overdue + timedelta(days=8)
    repo.complete_study(entry.id, StudyContext(later, False))
    repo.complete_study(entry.id, StudyContext(later, True))
    assert progress(repo, entry.id)['next_due_on'] == (later + timedelta(days=2)).isoformat()


def test_old_first_groups_monotonic_and_master_status_independent(repo):
    old = add(repo)[0]
    repo.prepare_study(AM)
    repo.set_flag(old.id, 'learned', True)
    add(repo, 12, prefix="new")
    tomorrow = StudyContext(DAY + timedelta(days=1), False)
    tasks = repo.prepare_study(tomorrow)
    assert tasks[0].entry.id == old.id and tasks[0].group_number == 1
    assert [task.group_number for task in tasks[1:]] == [2] * 10 + [3] * 2
    for task in tasks:
        repo.delete(task.entry.id)
    add(repo, prefix="afterdelete")
    tasks = repo.prepare_study(StudyContext(DAY + timedelta(days=2), False))
    assert tasks[0].group_number == 4


def test_edit_replace_import_and_delete_preserve_or_clear_progress(repo):
    entry = add(repo)[0]
    repo.prepare_study(AM)
    repo.complete_study(entry.id, AM)
    state = progress(repo, entry.id)
    repo.save(replace(entry, text=entry.text.upper()))
    assert progress(repo, entry.id) == state
    conflicts = repo.import_entries([WordEntry(entry.text, [Sense('v.', '新内容')])]).conflicts
    adopted = repo.replace_imports(conflicts)[entry.text.casefold()]
    assert adopted.id != entry.id
    assert progress(repo, adopted.id) == dict(state, word_id=adopted.id)
    repo.delete(adopted.id)
    assert repo.connection.execute('SELECT count(*) FROM memorize_vocabulary_17day').fetchone()[0] == 0


def test_batch_and_completion_failure_roll_back(repo):
    entry = add(repo, 2)[0]
    repo.connection.execute("""CREATE TRIGGER fail_study BEFORE INSERT ON memorize_vocabulary_17day
        WHEN NEW.position=2 BEGIN SELECT RAISE(ABORT,'batch failed'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.prepare_study(AM)
    assert repo.connection.execute("SELECT count(*) FROM settings WHERE key LIKE 'study17.%'").fetchone()[0] == 0
    assert repo.connection.execute("SELECT count(*) FROM memorize_vocabulary_17day").fetchone()[0] == 0
    repo.connection.execute('DROP TRIGGER fail_study')
    repo.prepare_study(AM)
    before = progress(repo, entry.id)
    repo.connection.execute("""CREATE TRIGGER fail_complete BEFORE UPDATE ON memorize_vocabulary_17day
        BEGIN SELECT RAISE(ABORT,'completion failed'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.complete_study(entry.id, AM)
    assert progress(repo, entry.id) == before


def test_import_failure_restores_progress_and_original(repo):
    entry = add(repo)[0]
    repo.prepare_study(AM)
    state = progress(repo, entry.id)
    conflicts = repo.import_entries([WordEntry(entry.text, [Sense('v.', '新内容')])]).conflicts
    repo.connection.execute("""CREATE TRIGGER fail_transfer BEFORE INSERT ON memorize_vocabulary_17day
        BEGIN SELECT RAISE(ABORT,'transfer failed'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_imports(conflicts)
    assert repo.get(entry.id) == entry
    assert progress(repo, entry.id) == state


def test_v2_migration_and_newer_version_rejected(tmp_path):
    path = tmp_path / 'v2.sqlite3'
    repo = Repository(path)
    entry = add(repo)[0]
    repo.write_shortcuts({'save': 'Alt+S'})
    repo.connection.executescript('DROP TABLE memorize_vocabulary_17day; PRAGMA user_version=2;')
    repo.close()
    repo = Repository(path)
    try:
        assert repo.connection.execute('PRAGMA user_version').fetchone()[0] == 3
        assert repo.get(entry.id) == entry
        assert repo.shortcuts()['save'] == 'Alt+S'
        assert len(repo.prepare_study(AM)) == 1
        repo.connection.execute('PRAGMA user_version=4')
    finally:
        repo.close()
    with pytest.raises(RuntimeError):
        Repository(path)


def enter(window, monkeypatch, context=AM):
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: context))
    window.set_filter('study17')
    return window.study_page


def test_study_ui_masks_groups_reveal_no_edit_or_master_actions(window, qtbot, monkeypatch):
    entries = add(window.repository, 2)
    window.search_box.setText('unmatched')
    page = enter(window, monkeypatch)
    assert not window.table.isVisible() and page.isVisible()
    assert not window.search_box.isVisible() and not window.editor_panel.isVisible()
    assert window.import_button.isVisible() and window.export_button.isVisible()
    assert window.import_button.accessibleName() == '导入学习进度'
    assert window.export_button.accessibleName() == '导出学习进度'
    assert window.global_hide_button.isVisible()
    assert len(page.model.rows) == 2 and all(row.hidden for row in page.model.rows)
    assert page.model.index(0, 0).data() == '1'
    assert not page.table.show_drag_handle and not page.table.shuffle_button.isVisible()
    assert [action for action, _ in page.table.action_rects(page.table.rect())] == ['study']
    index = page.model.index(0, 1)
    qtbot.mouseDClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=page.table.visualRect(index).center())
    assert page.table.delegate.editor is None
    assert not page.model.flags(index) & Qt.ItemFlag.ItemIsEditable
    first = page.model.rows[0].entry
    qtbot.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=page.table.visualRect(page.model.index(0, 0)).center())
    assert window.repository.get(first.id).important == first.important
    qtbot.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=page.table.visualRect(page.model.index(0, 2)).center())
    assert not page.model.rows[0].hidden and any(row.owner == 0 for row in page.model.rows)
    window.set_global_hidden(True)
    assert all(row.hidden for row in page.model.rows)
    rect = page.table.action_rects(page.table.visualRect(page.model.index(0, 4)))[0][1]
    qtbot.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert len(page.tasks) == 1
    assert progress(window.repository, first.id)['morning_done']
    assert all(not window.repository.get(e.id).learned for e in entries)
    window.set_filter('pending')
    assert window.search_box.text() == 'unmatched' and window.search_box.isVisible()
    assert window.table.isVisible() and not page.isVisible()


def test_page_clock_only_changes_on_refresh_and_reentry(window, qtbot, monkeypatch):
    entry = add(window.repository)[0]
    page = enter(window, monkeypatch)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: PM))
    window.set_global_hidden(False)
    page.complete(entry.id, 'study')
    assert progress(window.repository, entry.id)['morning_done'] == 1
    assert page.context == AM
    qtbot.mouseClick(page.refresh_button, Qt.MouseButton.LeftButton)
    assert page.context == PM and len(page.tasks) == 1
    page.complete(entry.id, 'study')
    assert progress(window.repository, entry.id)['last_completed_on'] == DAY.isoformat()
    assert page.tasks == []
    next_day = StudyContext(DAY + timedelta(days=1), False)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: next_day))
    window.refresh()
    assert page.context == PM
    window.set_filter('pending')
    window.set_filter('study17')
    assert page.context == next_day and len(page.tasks) == 1


def test_ui_completion_and_refresh_failures_keep_projection(window, monkeypatch):
    entry = add(window.repository)[0]
    page = enter(window, monkeypatch)
    def fail(*args):
        raise sqlite3.OperationalError('test write failure')
    monkeypatch.setattr(window.repository, 'complete_study', fail)
    page.complete(entry.id, 'study')
    assert len(page.tasks) == 1 and not progress(window.repository, entry.id)['morning_done']
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: PM))
    monkeypatch.setattr(window.repository, 'prepare_study', fail)
    assert not page.reload()
    assert page.context == AM and len(page.tasks) == 1

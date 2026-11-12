from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QToolTip

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import BEIJING, StudyContext, format_study_duration
from vocabulary.study_history import DURATION_COLUMN


DAY = date(2026, 11, 5)
AM = StudyContext(DAY, False)
PM = StudyContext(DAY, True)


def mixed_words(repo):
    words = [repo.save(WordEntry(f'word-{i}', [Sense('n.', '释义'), Sense('v.', '补充')],
                                [RelatedWord('child', [Sense('n.', '关联')])])) for i in range(3)]
    repo.prepare_study(AM)
    with repo.connection:
        repo.connection.execute('UPDATE memorize_vocabulary_17day SET stage=2,last_completed_on=? WHERE word_id=?',
                                ((DAY - timedelta(days=1)).isoformat(), words[1].id))
        repo.connection.execute('UPDATE memorize_vocabulary_17day SET stage=15,last_completed_on=? WHERE word_id=?',
                                ((DAY - timedelta(days=8)).isoformat(), words[2].id))
    return words


@pytest.mark.parametrize('seconds,text', [
    (0, '0 s'), (1, '1 s'), (59, '59 s'), (60, '1 m'), (61, '1 m 1 s'),
    (120, '2 m'), (3599, '59 m 59 s'), (3600, '1 h'), (3601, '1 h'),
    (3659, '1 h'), (3661, '1 h 1 m'), (7199, '1 h 59 m'), (7200, '2 h'),
    (86400, '24 h'), (90061, '25 h 1 m'),
])
def test_duration_format_boundaries(seconds, text):
    assert format_study_duration(seconds) == text


def test_learning_interval_hides_only_while_running(window, repo, qtbot, monkeypatch):
    word = repo.save(WordEntry('word', [Sense('n.', '释义')]))
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    assert page.interval_label.isVisible()
    assert page.interval_label.text() == '学习间隔： —'

    finished = datetime(2026, 11, 5, 13, 4, 31, tzinfo=BEIJING)
    now = finished + timedelta(hours=2, minutes=6, seconds=59)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr('vocabulary.study_page.datetime', FrozenDateTime)
    repo.complete_study(word.id, AM, instant=finished)
    assert page.load(AM)
    assert page.interval_label.isVisible()
    assert page.interval_label.text() == '学习间隔： 2 h 6 m'

    window.resize(window.minimumSize())
    assert page.load(PM)
    qtbot.waitUntil(lambda: page.timer_button.geometry().right() < page.interval_label.geometry().left())
    assert page.interval_label.isVisible()
    assert page.interval_label.text() == '学习间隔： 2 h 6 m'
    now += timedelta(seconds=1)
    page._clock_display.timeout.emit()
    assert page.interval_label.text() == '学习间隔： 2 h 6 m'
    assert page._interval_display.interval() == 70_000
    assert page._interval_display.isActive()
    page._interval_display.timeout.emit()
    assert page.interval_label.text() == '学习间隔： 2 h 7 m'

    page.toggle_timer()
    assert page.timer.running and page.interval_label.isHidden()
    page.toggle_timer()
    assert not page.timer.running and page.interval_label.isVisible()
    assert page.load(PM) and page.interval_label.isVisible()
    monkeypatch.setattr('vocabulary.storage.study_timestamp',
                        lambda instant=None: (instant or now).isoformat(timespec='seconds'))
    page.complete(word.id, 'study')
    assert page.timer.history.status == 1
    assert page.interval_label.isVisible()
    assert page.interval_label.text() == '学习间隔： 0 m'
    assert page.load(PM) and page.interval_label.isVisible()
    now += timedelta(minutes=2)
    page._interval_display.timeout.emit()
    assert page.interval_label.text() == '学习间隔： 2 m'
    window.set_filter('pending')
    assert not page._interval_display.isActive()


@pytest.mark.parametrize('gap,text', [
    (timedelta(seconds=-1), '—'), (timedelta(seconds=59), '0 m'),
    (timedelta(minutes=59), '59 m'), (timedelta(hours=1), '1 h'),
    (timedelta(hours=23, minutes=59), '23 h 59 m'), (timedelta(days=1), '1 d'),
    (timedelta(days=365, hours=2, minutes=3, seconds=59), '365 d 2 h 3 m'),
])
def test_learning_interval_units(window, repo, monkeypatch, gap, text):
    finished = datetime(2026, 11, 5, 13, 4, 31, tzinfo=BEIJING)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return (finished + gap).astimezone(tz)

    monkeypatch.setattr('vocabulary.study_page.datetime', FrozenDateTime)
    page = window.study_page
    page._last_study_end = finished
    page.update_interval()
    assert page.interval_label.text() == f'学习间隔： {text}'


def test_marks_do_not_write_duration_and_history_reads_only_saved_time(window, repo, monkeypatch):
    words = mixed_words(repo)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page, panel = window.study_page, window.history_panel
    panel.set_collapsed(False)
    ticks = [0]
    page.timer.clock = lambda: ticks[0]
    assert page.review_count.accessibleName() == '复习：剩余2/2'
    assert page.new_count.accessibleName() == '新词：剩余1/1'
    page.toggle_timer()
    repo.connection.execute('''CREATE TRIGGER no_duration_on_mark BEFORE UPDATE OF duration_seconds ON study_task_history
        BEGIN SELECT RAISE(ABORT,'duration was updated'); END''')
    ticks[0] = 5_400_000_000
    page.complete(words[0].id, 'study')
    assert len(page.tasks) == 2
    assert page.new_count.accessibleName() == '新词：剩余0/1'
    assert page.elapsed_label.text() == '5 s'
    assert repo.study_task_history(AM).duration_seconds == 0
    panel.update_live()
    assert panel.model.index(0, DURATION_COLUMN).data() == '0 s'
    repo.connection.execute('DROP TRIGGER no_duration_on_mark')
    page.toggle_timer()
    assert panel.model.index(0, DURATION_COLUMN).data() == '5 s'
    page.toggle_timer()
    ticks[0] += 1_600_000_000
    page.complete(words[1].id, 'study')
    assert page.review_count.accessibleName() == '复习：剩余1/2'
    assert repo.study_task_history(AM).duration_seconds == 5
    page.complete(words[2].id, 'study')
    assert repo.study_task_history(AM).duration_seconds == 7
    assert panel.model.index(0, DURATION_COLUMN).data() == '7 s'
    assert page.elapsed_label.text() == '7 s' and not page.timer.running


def test_completed_words_are_below_pending_revealed_and_have_no_mark_actions(window, repo, monkeypatch, qtbot):
    words = mixed_words(repo)
    repo.complete_study(words[0].id, AM)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    assert not page.show_completed and page.completed_button.toolTip() == '显示已完成'
    qtbot.mouseClick(page.completed_button, Qt.MouseButton.LeftButton)
    assert page.show_completed and page.completed_button.toolTip() == '隐藏已完成'
    roots = [row for row in page.model.rows if row.first]
    assert [row.entry.id for row in roots] == [words[1].id, words[2].id, words[0].id]
    assert all(not row.hidden for row in page.model.rows if row.entry.id == words[0].id)
    index = next(page.model.index(i, page.model.action_column) for i, row in enumerate(page.model.rows)
                 if row.first and row.entry.id == words[0].id)
    assert index.data(Qt.ItemDataRole.AccessibleTextRole) == '复制单词完整内容'
    assert [action for action, _ in page.table.action_rects(page.table.visualRect(index))] == ['copy']
    requests = []
    page.table.actionRequested.connect(lambda *args: requests.append(args))
    page.table.select_entry(words[0].id, scroll=True)
    page.table.setFocus()
    qtbot.keyClick(page.table, Qt.Key.Key_Return)
    qtbot.keyClick(page.table, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    qtbot.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=page.table.visualRect(index).center())
    assert not requests and not page.timer.running
    qtbot.keyClick(page.table, Qt.Key.Key_Space)
    qtbot.keyClick(page.table, Qt.Key.Key_Space)
    assert not page.timer.running  # Reading a completed word never starts the task clock.
    page.complete(words[1].id, 'study')
    assert page.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == words[2].id
    assert [row.entry.id for row in page.model.rows if row.first] == [words[2].id, words[0].id, words[1].id]
    page.complete(words[2].id, 'study')
    assert not page.tasks and len([row for row in page.model.rows if row.first]) == 3
    assert not page.table.currentIndex().isValid()
    qtbot.mouseClick(page.completed_button, Qt.MouseButton.LeftButton)
    assert page.model.rowCount() == 0


def test_completed_query_survives_stage_advancement_and_keeps_counter_categories(repo):
    words = mixed_words(repo)
    for word in words:
        repo.complete_study(word.id, AM)
    assert [task.entry.id for task in repo.study_completed_tasks(AM)] == [word.id for word in words]
    assert repo.study_completed_tasks(PM) == []
    for word in words:
        repo.complete_study(word.id, PM)
    completed = repo.study_completed_tasks(PM)
    assert [task.entry.id for task in completed] == [word.id for word in words]
    assert [task.is_review for task in completed] == [False, True, True]
    assert repo.study_task_history(PM).new_done == 1
    assert repo.study_task_history(PM).review_done == 2


def test_study_stage_column_shows_the_stored_stage_before_and_after_completion(window, repo, monkeypatch):
    words = mixed_words(repo)
    for word in words:
        repo.complete_study(word.id, AM)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: PM))
    window.set_filter('study17')
    page = window.study_page
    model = page.model
    assert [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())] == [
        '组', '单词', '词性', '翻译', '周期', '操作']
    assert window.model.columnCount() == 5
    assert all(row.hidden for row in model.rows)
    assert [model.index(i, 4).data() for i in range(model.rowCount())] == ['1', '2', '15']
    assert model.index(0, 4).data(Qt.ItemDataRole.TextAlignmentRole) == Qt.AlignmentFlag.AlignCenter
    assert not model.flags(model.index(0, 4)) & Qt.ItemFlag.ItemIsEditable

    page.toggle_completed(True)
    page.complete(words[2].id, 'study')
    for reload in (False, True):
        if reload:
            assert page.load(PM)
        stages = [model.index(i, 4).data() for i, row in enumerate(model.rows) if row.entry.id == words[2].id]
        assert stages == ['0', None, None]


def test_session_buttons_order_and_group_column_has_no_tooltip(window, repo, monkeypatch, qtbot):
    mixed_words(repo)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    qtbot.wait(20)
    page = window.study_page
    controls = (page.refresh_button, page.period_label, page.elapsed_icon, page.elapsed_label, page.timer_button, page.interval_label,
                page.review_count, page.new_count, page.completed_button)
    assert all(left.geometry().right() < right.geometry().left() for left, right in zip(controls, controls[1:]))
    index = page.model.index(0, 0)
    assert index.data(Qt.ItemDataRole.DisplayRole) and index.data(Qt.ItemDataRole.ToolTipRole) is None
    shown = []
    monkeypatch.setattr(QToolTip, 'showText', lambda *args: shown.append(args))
    point = page.table.visualRect(index).center()
    event = QHelpEvent(QEvent.Type.ToolTip, point, page.table.viewport().mapToGlobal(point))
    assert page.table.viewportEvent(event) and not shown


def test_mark_resuming_failed_pause_retries_saved_time_without_losing_it(window, repo, monkeypatch):
    words = mixed_words(repo)
    window.study_page.load(AM)
    page = window.study_page
    ticks = [0]
    page.timer.clock = lambda: ticks[0]
    page.toggle_timer()
    ticks[0] = 5_400_000_000
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'save_study_duration', lambda *args: (_ for _ in ()).throw(OSError('locked')))
        page.timer.suspend()
    assert not page.timer.running and repo.study_task_history(AM).duration_seconds == 0
    ticks[0] += 100_000_000_000
    page.complete(words[0].id, 'study')
    assert page.timer.running and len(page.tasks) == 2
    assert page.timer.duration_seconds() == repo.study_task_history(AM).duration_seconds == 5


def test_live_elapsed_clock_ticks_without_writing_history_and_stops_only_its_display_when_hidden(
        window, repo, monkeypatch, qtbot):
    mixed_words(repo)
    repo.start_study_task(AM)
    repo.save_study_duration(AM, 42)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    ticks = [0]
    page.timer.clock = lambda: ticks[0]
    assert page.elapsed_label.text() == '42 s'
    assert page.elapsed_label.accessibleName() == '用时：42 s'
    assert not page.elapsed_icon.pixmap().isNull()
    assert page._clock_display.isActive()
    page.toggle_timer()
    ticks[0] = 1_600_000_000
    qtbot.waitUntil(lambda: page.elapsed_label.text() == '44 s', timeout=1800)
    assert repo.study_task_history(AM).duration_seconds == 42
    window.set_filter('pending')
    assert not page._clock_display.isActive() and page.timer.running
    ticks[0] += 50_000_000_000
    window.set_filter('study17')
    assert page.elapsed_label.text() == '1 m 34 s'
    assert repo.study_task_history(AM).duration_seconds == 42


def test_completed_task_hides_timer_and_restores_it_for_next_period(window, repo, monkeypatch, qtbot):
    entry = repo.save(WordEntry('word', [Sense('n.', '释义')]))
    context = [AM]
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: context[0]))
    window.set_filter('study17')
    page = window.study_page
    controls = (page.elapsed_icon, page.elapsed_label, page.timer_button)
    assert all(control.isVisible() for control in controls)
    page.toggle_timer()
    assert page.timer.running and all(control.isVisible() for control in controls)
    page.toggle_timer()
    assert not page.timer.running and all(control.isVisible() for control in controls)
    page.complete(entry.id, 'study')
    assert repo.study_task_history(AM).status == 1
    assert not page.timer.running
    assert all(control.isHidden() for control in controls)
    assert page.refresh_button.isVisible() and page.period_label.isVisible()
    assert page.review_count.isVisible() and page.new_count.isVisible()
    assert page.completed_button.isVisible()
    page.toggle_completed(True)
    assert page.model.rowCount() > 0 and all(control.isHidden() for control in controls)
    assert page.reload() and all(control.isHidden() for control in controls)
    window.set_filter('pending')
    window.set_filter('study17')
    assert all(control.isHidden() for control in controls)
    context[0] = PM
    assert page.reload()
    assert all(control.isVisible() for control in controls)
    assert page.timer_button.isEnabled() and page.timer_button.toolTip() == '开始背单词'
    page.complete(entry.id, 'study')
    assert all(control.isHidden() for control in controls)


def test_loading_completed_record_hides_timer_immediately(window, repo, monkeypatch):
    entry = repo.save(WordEntry('word', [Sense('n.', '释义')]))
    repo.prepare_study(AM)
    repo.complete_study(entry.id, AM)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    assert page.timer.history.status == 1
    assert page.completion_notice.isVisible()
    assert all(control.isHidden() for control in (page.elapsed_icon, page.elapsed_label, page.timer_button))


@pytest.mark.parametrize('started', [False, True])
def test_empty_incomplete_task_does_not_hide_timer(window, repo, monkeypatch, started):
    entry = repo.save(WordEntry('word', [Sense('n.', '释义')])) if started else None
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    if started:
        page.toggle_timer()
        repo.delete(entry.id, AM)
        assert page.load(AM)
        assert page.timer.history.status == 0
    else:
        assert page.timer.history is None
    assert not page.tasks and not page.timer_button.isEnabled()
    assert not page.completion_notice.isVisible()
    assert all(control.isVisible() for control in (page.elapsed_icon, page.elapsed_label, page.timer_button))


def test_failed_completion_keeps_timer_visible(window, repo, monkeypatch):
    entry = repo.save(WordEntry('word', [Sense('n.', '释义')]))
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    def fail(*args, **kwargs):
        raise OSError('locked')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'complete_study', fail)
        page.complete(entry.id, 'study')
    assert all(control.isVisible() for control in (page.elapsed_icon, page.elapsed_label, page.timer_button))
    assert page.tasks
    assert not page.completion_notice.isVisible()
    page.complete(entry.id, 'study')
    assert all(control.isHidden() for control in (page.elapsed_icon, page.elapsed_label, page.timer_button))

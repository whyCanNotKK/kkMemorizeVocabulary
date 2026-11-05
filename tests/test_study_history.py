from dataclasses import replace
from datetime import date, timedelta

import pytest
from PySide6.QtCore import Qt

from vocabulary.learning import StudyContext, StudyTaskHistory
from vocabulary.study_history import HistoryModel


DAY = date(2026, 11, 5)
AM = StudyContext(DAY, False)
PM = StudyContext(DAY, True)


def record(day=DAY, afternoon=0, **changes):
    value = StudyTaskHistory(1, day.isoformat(), afternoon, f'{day.isoformat()}T08:00:00+08:00',
                             None, 1, 0, 5, 7, 2, 3)
    return replace(value, **changes)


def text(model, row, column):
    return model.data(model.index(row, column))


def status(model, row):
    return model.data(model.index(row, 7), Qt.ItemDataRole.AccessibleTextRole)


def test_empty_shows_today_only_and_today_afternoon_is_omitted_until_1500(qapp):
    model = HistoryModel()
    model.load([], AM)
    assert model.rowCount() == 1
    assert text(model, 0, 0) == DAY.isoformat()
    assert text(model, 0, 1) == '上午'
    assert status(model, 0) == '完全没背单词'
    model.load([record(afternoon=1)], AM)
    assert model.rowCount() == 1 and text(model, 0, 1) == '上午'
    model.load([record(DAY + timedelta(days=1))], AM)
    assert model.rowCount() == 1  # The displayed range ends today even for a clock-adjusted import.
    model.load([], PM)
    assert model.rowCount() == 2
    assert [text(model, row, 1) for row in range(2)] == ['下午', '上午']


def test_gaps_are_virtual_dates_descending_and_periods_paired(qapp):
    model = HistoryModel()
    earliest = DAY - timedelta(days=2)
    model.load([record(earliest), record(afternoon=1, id=2)], PM)
    assert model.rowCount() == 6
    assert [model.period(row) for row in range(6)] == [
        (DAY, 1), (DAY, 0), (DAY - timedelta(days=1), 1),
        (DAY - timedelta(days=1), 0), (earliest, 1), (earliest, 0)]
    assert status(model, 0) == '当前任务未完成'
    assert text(model, 1, 0) == ''  # Date is shared visually by the pair.
    assert status(model, 2) == status(model, 3) == '完全没背单词'
    assert text(model, 5, 4) == '1 s'
    assert text(model, 5, 5) == '3/7' and text(model, 5, 6) == '2/5'
    assert model.HEADERS == ('日期', '时段', '开始时间', '结束时间', '有效用时', '复习词', '新词', '状态')
    assert model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole) == model.data(
        model.index(1, 0), Qt.ItemDataRole.BackgroundRole)
    model.load([record(earliest)], AM)
    assert model.rowCount() == 5
    assert model.period(0) == (DAY, 0) and model.period(1) == (DAY - timedelta(days=1), 1)


def test_panel_starts_collapsed_keeps_independent_state_and_does_not_create_tasks(window, repo, monkeypatch, qtbot):
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    panel = window.history_panel
    assert not window.form_collapsed
    window.set_filter('study17')
    assert panel.isVisible() and panel.collapsed and not panel.table.isVisible()
    qtbot.mouseClick(panel.collapse_button, Qt.MouseButton.LeftButton)
    assert not panel.collapsed and panel.table.isVisible()
    assert panel.height() <= window.centralWidget().height() // 2
    assert status(panel.model, 0) == '完全没背单词'
    assert repo.study_task_histories() == []
    window.set_filter('pending')
    assert not panel.isVisible() and not window.form_collapsed and window.editor_body.isVisible()
    window.set_form_collapsed(True)
    window.set_filter('study17')
    assert not panel.collapsed and panel.table.isVisible()
    qtbot.mouseClick(panel.collapse_button, Qt.MouseButton.LeftButton)
    window.set_filter('pending')
    assert window.form_collapsed


def test_history_live_timer_and_completion_refresh(window, repo, monkeypatch, qtbot, tmp_path):
    from vocabulary.domain import Sense, WordEntry
    word = repo.save(WordEntry('example', [Sense('n.', '例子')]))
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page, panel = window.study_page, window.history_panel
    panel.set_collapsed(False)
    ticks = [0]
    page.timer.clock = lambda: ticks[0]
    page.toggle_timer()
    assert status(panel.model, 0) == '当前任务未完成'
    ticks[0] = 1234_000_000
    panel.update_live()
    assert text(panel.model, 0, 4) == '0 s'
    page.update_elapsed()
    assert page.elapsed_label.text() == '1 s'
    assert repo.study_task_history(AM).duration_seconds == 0  # Displaying history does not write time.
    page.complete(word.id, 'study')
    assert status(panel.model, 0) == '已完成'
    assert text(panel.model, 0, 6) == '1/1'
    assert text(panel.model, 0, 4) == '1 s'
    qtbot.wait(20)
    window.grab().save(str(tmp_path / 'study-history.png'))


def test_live_clock_adds_afternoon_at_boundary(window, monkeypatch):
    now = [AM]
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: now[0]))
    window.set_filter('study17')
    panel = window.history_panel
    panel.set_collapsed(False)
    assert panel.model.rowCount() == 1
    now[0] = PM
    panel.update_live()
    assert panel.model.rowCount() == 2 and text(panel.model, 0, 1) == '下午'
    assert window.study_page.context == AM  # History does not change the task's frozen context.


@pytest.mark.parametrize('kind,label', [
    ('missing', '完全没背单词'), ('completed', '已完成'),
    ('unfinished', '历史任务未完成'), ('current', '当前任务未完成'),
])
def test_status_last_column_uses_icons_and_preserves_accessible_meaning(qapp, kind, label):
    model = HistoryModel()
    records = [] if kind == 'missing' else [record()]
    if kind == 'completed':
        records = [record(status=1, ended_at=f'{DAY.isoformat()}T09:12:34+08:00')]
    context = PM if kind == 'unfinished' else AM
    model.load(records, AM, context)
    index = model.index(0, 7)
    assert index.data() == ''
    assert not index.data(Qt.ItemDataRole.DecorationRole).isNull()
    assert index.data(Qt.ItemDataRole.ToolTipRole) == label
    assert index.data(Qt.ItemDataRole.AccessibleTextRole) == label


def test_start_end_time_hide_seconds_and_retain_complete_tooltips(qapp):
    model = HistoryModel()
    start = f'{DAY.isoformat()}T08:12:59+08:00'
    end = f'{(DAY + timedelta(days=1)).isoformat()}T00:03:41+08:00'
    model.load([record(started_at=start, ended_at=end, status=1)], AM)
    assert text(model, 0, 2) == '08:12'
    assert text(model, 0, 3) == '11-06 00:03'
    assert model.data(model.index(0, 2), Qt.ItemDataRole.ToolTipRole) == start
    assert model.data(model.index(0, 3), Qt.ItemDataRole.ToolTipRole) == end
    model.load([record()], AM)
    assert text(model, 0, 3) == '—'


def test_current_status_follows_frozen_task_even_when_paused_or_clock_passes_1500(window, repo, monkeypatch):
    from vocabulary.domain import Sense, WordEntry
    repo.save(WordEntry('word', [Sense('n.', '释义')]))
    now = [AM]
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: now[0]))
    window.set_filter('study17')
    page, panel = window.study_page, window.history_panel
    page.toggle_timer()
    page.toggle_timer()
    panel.set_collapsed(False)
    assert not page.timer.running and status(panel.model, 0) == '当前任务未完成'
    now[0] = PM
    panel.update_live()
    assert status(panel.model, 0) == '完全没背单词'
    assert status(panel.model, 1) == '当前任务未完成'
    page.reload()
    assert status(panel.model, 1) == '历史任务未完成'

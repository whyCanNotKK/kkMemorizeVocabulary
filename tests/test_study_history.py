from dataclasses import replace
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QStyleOptionViewItem

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext, StudyTaskHistory
from vocabulary.storage import Repository
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


def period_label(model, row):
    return model.index(row, 1).data(Qt.ItemDataRole.ToolTipRole)


def test_empty_shows_today_only_and_today_afternoon_is_omitted_until_1500(qapp):
    model = HistoryModel()
    model.load([], AM)
    assert model.rowCount() == 1
    assert text(model, 0, 0) == DAY.isoformat()
    assert period_label(model, 0) == '上午'
    assert status(model, 0) == '完全没背单词'
    model.load([record(afternoon=1)], AM)
    assert model.rowCount() == 1 and period_label(model, 0) == '上午'
    model.load([record(DAY + timedelta(days=1))], AM)
    assert model.rowCount() == 1  # The displayed range ends today even for a clock-adjusted import.
    model.load([], PM)
    assert model.rowCount() == 2
    assert [period_label(model, row) for row in range(2)] == ['上午', '下午']


def test_gaps_are_virtual_dates_descending_and_periods_paired(qapp):
    model = HistoryModel()
    earliest = DAY - timedelta(days=2)
    model.load([record(earliest), record(afternoon=1, id=2)], PM)
    assert model.rowCount() == 6
    assert [model.period(row) for row in range(6)] == [
        (DAY, 0), (DAY, 1), (DAY - timedelta(days=1), 0),
        (DAY - timedelta(days=1), 1), (earliest, 0), (earliest, 1)]
    assert status(model, 1) == '当前任务未完成'
    assert text(model, 1, 0) == ''  # Date is shared visually by the pair.
    assert status(model, 2) == status(model, 3) == '完全没背单词'
    assert text(model, 4, 4) == '1 s'
    assert text(model, 4, 5) == '3 / 7' and text(model, 4, 6) == '2 / 5'
    assert model.HEADERS == ('日期', '时段', '开始时间', '结束时间', '有效用时', '复习词', '新词', '状态')
    assert model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole) == model.data(
        model.index(1, 0), Qt.ItemDataRole.BackgroundRole)
    model.load([record(earliest)], AM)
    assert model.rowCount() == 5
    assert model.period(0) == (DAY, 0) and model.period(1) == (DAY - timedelta(days=1), 0)


def test_period_icons_are_colored_with_tooltips_and_keep_their_own_records(qapp):
    model = HistoryModel()
    model.load([record(duration_seconds=60), record(afternoon=1, id=2, duration_seconds=3600)], PM)
    images = []
    for row, label in enumerate(('上午', '下午')):
        index = model.index(row, 1)
        assert index.data() == ''
        assert index.data(Qt.ItemDataRole.ToolTipRole) == label
        assert index.data(Qt.ItemDataRole.AccessibleTextRole) == label
        image = index.data(Qt.ItemDataRole.DecorationRole).pixmap(24, 24).toImage()
        pixels = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
                  if image.pixelColor(x, y).alpha() > 200]
        assert pixels
        assert any(color.red() > color.blue() if row == 0 else color.blue() > color.red()
                   for color in pixels)
        images.append(image)
    assert images[0] != images[1]
    assert text(model, 0, 4) == '1 m'
    assert text(model, 1, 4) == '1 h'


def test_participant_count_includes_finished_future_and_learned_words_but_not_related(repo):
    assert repo.study_participant_count() == 0
    entries = [repo.save(WordEntry(f'word-{i}', [Sense('n.', '')],
                                  [RelatedWord('child', [Sense('n.', '')])], learned=i == 2))
               for i in range(4)]
    with repo.connection:
        repo.connection.executemany('''INSERT INTO memorize_vocabulary_17day
            (word_id,group_number,stage,last_completed_on) VALUES (?,?,?,?)''', [
                (entries[0].id, 1, 1, None), (entries[1].id, 1, 15, DAY.isoformat()),
                (entries[2].id, 2, 0, DAY.isoformat())])
    assert repo.study_participant_count() == 3
    assert len(repo.study_tasks(AM)) == 1
    reopened = Repository(repo.path)
    try:
        assert reopened.study_participant_count() == 3
    finally:
        reopened.close()


@pytest.mark.parametrize('collapsed', [False, True])
def test_history_header_count_stays_visible_with_matching_counter_style(window, repo, monkeypatch, collapsed, tmp_path, qtbot):
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: PM))
    window.resize(860, 640)
    qtbot.wait(10)
    entry = repo.save(WordEntry('word', [Sense('n.', '')]))
    window.set_filter('study17')
    panel = window.history_panel
    panel.set_collapsed(collapsed)
    assert panel.total_count.isVisible()
    assert panel.total_count.accessibleName() == '合计 0 个单词'
    with repo.connection:
        repo.connection.execute('''INSERT INTO memorize_vocabulary_17day
            (word_id,group_number,stage,last_completed_on) VALUES (?,1,0,?)''', (entry.id, DAY.isoformat()))
    panel.refresh()
    qtbot.wait(20)
    assert panel.total_count.accessibleName() == '合计 1 个单词'
    assert panel.total_count.toolTip() == '包括已经背完的和正在背的'
    assert panel.total_count.textFormat() == Qt.TextFormat.RichText
    for label in (panel.total_count, window.study_page.review_count, window.study_page.new_count):
        assert 'font-size:22px;font-weight:700' in label.text()
    assert panel.total_count.geometry().left() > panel.collapse_button.geometry().right()
    assert panel.total_count.sizeHint().width() <= panel.total_count.width()
    assert panel.geometry().bottom() < window.centralWidget().height()
    window.grab().save(str(tmp_path / 'history-total.png'))


def test_history_total_updates_on_draw_completion_clear_restore_delete_and_reentry(window, repo, monkeypatch):
    now = [PM]
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: now[0]))
    entry = repo.save(WordEntry('word', [Sense('n.', '词')]))
    window.set_filter('study17')
    page, panel = window.study_page, window.history_panel
    assert panel.collapsed and panel.total_count.accessibleName() == '合计 0 个单词'
    now[0] = AM
    page.reload()
    assert panel.total_count.accessibleName() == '合计 1 个单词'
    backup = repo.study_backup()
    page.complete(entry.id, 'study')
    now[0] = PM
    page.reload()
    page.complete(entry.id, 'study')
    assert not page.tasks and panel.total_count.accessibleName() == '合计 1 个单词'
    window.clear_content('delete study record')
    assert panel.total_count.accessibleName() == '合计 0 个单词'
    repo.replace_study_progress(backup)
    page.load(PM)
    assert panel.total_count.accessibleName() == '合计 1 个单词'
    repo.delete(entry.id, PM)
    page.load(PM)
    assert panel.total_count.accessibleName() == '合计 0 个单词'
    window.set_filter('pending')
    window.set_filter('study17')
    assert panel.total_count.accessibleName() == '合计 0 个单词'


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
    assert text(panel.model, 0, 6) == '1 / 1'
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
    assert panel.model.rowCount() == 2
    assert [period_label(panel.model, row) for row in range(2)] == ['上午', '下午']
    assert panel.table.rowSpan(0, 0) == 2
    assert window.study_page.context == AM  # History does not change the task's frozen context.


@pytest.mark.parametrize('context', [AM, PM])
def test_dates_merge_only_same_day_and_center_across_periods(window, context, tmp_path):
    window.set_filter('study17')
    panel = window.history_panel
    panel.set_collapsed(False)
    model, table = panel.model, panel.table
    model.load([record(DAY - timedelta(days=2))], context)
    panel.update_height()
    row = 0
    while row < model.rowCount():
        day, _ = model.period(row)
        span = 1 if day == DAY and not context.afternoon else 2
        assert table.rowSpan(row, 0) == span
        assert table.columnSpan(row, 0) == 1
        index = model.index(row, 0)
        assert index.data() == day.isoformat()
        assert index.data(Qt.ItemDataRole.TextAlignmentRole) == Qt.AlignmentFlag.AlignCenter
        rect = table.visualRect(index)
        assert rect.height() == sum(table.rowHeight(r) for r in range(row, row + span))
        if span == 2:
            assert rect == table.visualRect(model.index(row + 1, 0))
        for column in range(1, model.columnCount()):
            assert table.rowSpan(row, column) == 1
        row += span
    table.grab().save(str(tmp_path / 'merged-history-dates.png'))


def test_date_spans_rebuild_after_refresh_and_day_changes(window, monkeypatch):
    now = [PM]
    records = [record(DAY - timedelta(days=1))]
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: now[0]))
    monkeypatch.setattr(window.repository, 'study_task_histories', lambda: records)
    window.set_filter('study17')
    panel = window.history_panel
    panel.set_collapsed(False)
    assert panel.table.rowSpan(0, 0) == panel.table.rowSpan(2, 0) == 2
    now[0] = StudyContext(DAY + timedelta(days=1), False)
    panel.update_live()
    assert panel.table.rowSpan(0, 0) == 1
    assert panel.table.rowSpan(1, 0) == panel.table.rowSpan(3, 0) == 2
    records.clear()
    panel.refresh()
    assert panel.model.rowCount() == 1
    assert panel.table.rowSpan(0, 0) == 1


@pytest.mark.parametrize('kind,tooltip,label', [
    ('missing', '未完成', '完全没背单词'), ('completed', '已完成', '已完成'),
    ('unfinished', '未完成', '历史任务未完成'), ('current', '进行中', '当前任务未完成'),
])
def test_status_last_column_uses_icons_and_preserves_accessible_meaning(qapp, kind, tooltip, label):
    model = HistoryModel()
    records = [] if kind == 'missing' else [record()]
    if kind == 'completed':
        records = [record(status=1, ended_at=f'{DAY.isoformat()}T09:12:34+08:00')]
    context = PM if kind == 'unfinished' else AM
    model.load(records, AM, context)
    index = model.index(0, 7)
    assert index.data() == ''
    assert not index.data(Qt.ItemDataRole.DecorationRole).isNull()
    assert index.data(Qt.ItemDataRole.ToolTipRole) == tooltip
    assert index.data(Qt.ItemDataRole.AccessibleTextRole) == label


def test_date_tooltip_shows_weekday_for_both_periods(qapp):
    model = HistoryModel()
    model.load([record(date(2026, 11, 2))], StudyContext(date(2026, 11, 8), True))
    assert model.rowCount() == 14
    for row, weekday in enumerate(('周日', '周六', '周五', '周四', '周三', '周二', '周一')):
        for period in (0, 1):
            assert model.index(row * 2 + period, 0).data(Qt.ItemDataRole.ToolTipRole) == weekday


def test_history_duration_centers_groups_and_reserves_two_digits(window, qtbot):
    durations = (1, 10, 60, 600, 3600, 36000, 61, 601, 3660, 36600)
    window.resize(window.minimumSize())
    window.set_filter('study17')
    panel = window.history_panel
    panel.set_collapsed(False)
    model, table = panel.model, panel.table
    model.load([record(DAY - timedelta(days=i), duration_seconds=value)
                for i, value in enumerate(durations)], PM)
    qtbot.wait(20)
    option = QStyleOptionViewItem()
    table.initViewItemOption(option)
    option.rect = QRect(0, 0, table.columnWidth(4), table.rowHeight(0))

    def ink_bounds(row):
        image = QImage(option.rect.size(), QImage.Format.Format_RGB32)
        painter = QPainter(image)
        table.itemDelegate().paint(painter, option, model.index(row, 4))
        painter.end()
        columns = [x for x in range(image.width())
                   if any(image.pixelColor(x, y).red() > 100 for y in range(image.height()))]
        return min(columns), max(columns)

    bounds = [ink_bounds(2 * i) for i in range(len(durations))]
    for first, second in zip(bounds[::2], bounds[1::2]):
        assert first[1] == second[1]  # Adding a digit keeps the unit in place.
    for left, right in bounds:
        assert (left + right) / 2 == pytest.approx(option.rect.center().x(), abs=8)
    assert bounds[6][1] > bounds[0][1]  # Only displayed pairs take up space.
    assert bounds[8][1] > bounds[2][1]
    assert table.horizontalScrollBar().maximum() == 0


def test_history_duration_paints_all_digits_at_minimum_window_width(window, qtbot, monkeypatch):
    window.resize(window.minimumSize())
    window.set_filter('study17')
    panel = window.history_panel
    panel.set_collapsed(False)
    model, table = panel.model, panel.table
    model.load([record(duration_seconds=7140), record(afternoon=1, duration_seconds=1368)], PM)
    qtbot.wait(20)
    option = QStyleOptionViewItem()
    table.initViewItemOption(option)
    option.rect = QRect(0, 0, table.columnWidth(4), table.rowHeight(0))

    drawn = []
    draw_text = QPainter.drawText

    def capture(painter, *args):
        drawn.append(args[-1])
        return draw_text(painter, *args)

    monkeypatch.setattr(QPainter, 'drawText', capture)
    image = QImage(option.rect.size(), QImage.Format.Format_RGB32)
    painter = QPainter(image)
    try:
        for row in range(2):
            table.itemDelegate().paint(painter, option, model.index(row, 4))
    finally:
        painter.end()
    assert drawn == ['1', 'h', '59', 'm', '22', 'm', '48', 's']
    assert table.columnWidth(2) == table.columnWidth(3) < table.columnWidth(4)


def test_history_count_slash_stays_centered_for_different_digit_counts(window, qtbot):
    window.resize(window.minimumSize())
    window.set_filter('study17')
    panel = window.history_panel
    panel.set_collapsed(False)
    model, table = panel.model, panel.table
    counts = ((2, 7), (22, 230), (230, 230), (1234, 12000))
    model.load([record(DAY - timedelta(days=i), review_done=done, review_total=total,
                       new_done=done, new_total=total)
                for i, (done, total) in enumerate(counts)], PM)
    qtbot.wait(20)
    option = QStyleOptionViewItem()
    table.initViewItemOption(option)

    def slash_pixels(row, column):
        option.rect = QRect(0, 0, table.columnWidth(column), table.rowHeight(row))
        image = QImage(option.rect.size(), QImage.Format.Format_RGB32)
        painter = QPainter(image)
        table.itemDelegate().paint(painter, option, model.index(row, column))
        painter.end()
        center = option.rect.center().x()
        half_width = option.fontMetrics.horizontalAdvance('/') // 2 + 1
        return {(x - center, y) for x in range(center - half_width, center + half_width + 1)
                for y in range(image.height()) if image.pixelColor(x, y).red() > 100}

    for column in (5, 6):
        reference = slash_pixels(0, column)
        assert reference
        for i in range(1, len(counts)):
            assert slash_pixels(2 * i, column) == reference


def test_times_hide_seconds_and_value_cells_have_no_tooltips(qapp):
    model = HistoryModel()
    start = f'{DAY.isoformat()}T08:12:59+08:00'
    end = f'{(DAY + timedelta(days=1)).isoformat()}T00:03:41+08:00'
    model.load([record(started_at=start, ended_at=end, status=1)], AM)
    assert text(model, 0, 2) == '08：12'
    assert text(model, 0, 3) == '11-06 00：03'
    for column in range(2, 7):
        assert model.index(0, column).data(Qt.ItemDataRole.ToolTipRole) is None
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
    assert status(panel.model, 0) == '当前任务未完成'
    assert status(panel.model, 1) == '完全没背单词'
    page.reload()
    assert status(panel.model, 0) == '历史任务未完成'

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette

from vocabulary.domain import Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.study_history import EFFICIENCY_COLUMN, TOTAL_COLUMN


AM = StudyContext(date(2026, 11, 5), False)
PM = StudyContext(AM.day, True)


def finish(window, repo, monkeypatch):
    now = [AM]
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: now[0]))
    entry = repo.save(WordEntry('word', [Sense('n.', '词')]))
    window.set_filter('study17')
    page = window.study_page
    assert not page.completion_notice.isVisible()
    start = datetime.fromisoformat(f'{AM.day}T08:00:00+08:00')
    end = datetime.fromisoformat(f'{AM.day}T09:02:30+08:00')
    repo.start_study_task(AM, instant=start)
    repo.save_study_duration(AM, 2851)
    assert page.load(AM)
    monkeypatch.setattr(page.timer, 'clock', lambda: 0)
    complete = repo.complete_study
    monkeypatch.setattr(repo, 'complete_study', lambda *args, **kwargs: complete(*args, instant=end, **kwargs))
    page.set_global_hidden(False)
    page.complete(entry.id, 'study')
    assert page.completion_notice.isVisible()
    return page, entry, now


@pytest.mark.parametrize('target', ['toolbar', 'icon', 'link'])
def test_all_refresh_controls_use_same_entry_and_capture_next_period(window, repo, monkeypatch, qtbot, target):
    page, entry, now = finish(window, repo, monkeypatch)
    requested = []
    prepare = repo.prepare_study
    monkeypatch.setattr(repo, 'prepare_study', lambda context, **kwargs: (
        requested.append(context), prepare(context, **kwargs))[1])
    now[0] = PM
    control = {'toolbar': page.refresh_button, 'icon': page.completion_notice.refresh_icon,
               'link': page.completion_notice.refresh_link}[target]
    qtbot.mouseClick(control, Qt.MouseButton.LeftButton)
    assert requested == [PM] and page.context == PM
    assert [task.entry.id for task in page.tasks] == [entry.id]
    assert not page.completion_notice.isVisible()
    assert page.timer_button.isVisible() and not page.timer.running


def test_notice_has_exact_two_lines_clickable_styled_controls_and_tracks_viewport(window, repo, monkeypatch, qtbot, tmp_path):
    page, _, _ = finish(window, repo, monkeypatch)
    notice = page.completion_notice
    assert notice.title.text() == '当前时段任务完成，总时长 1 h 3 m，有效用时 47 m，效率 76%'
    assert notice.instruction.text() == '在新时段点击'
    assert notice.suffix.text() == '，开启新任务'
    assert notice.refresh_link.text() == '刷新' and notice.refresh_link.font().underline()
    assert notice.refresh_link.palette().color(QPalette.ColorRole.ButtonText) == QColor('#58a6ff')
    assert notice.refresh_icon.iconSize() == page.refresh_button.iconSize()
    assert notice.refresh_icon.icon().cacheKey() == page.refresh_button.icon().cacheKey()
    for control in (notice.refresh_icon, notice.refresh_link):
        assert control.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert control.isEnabled() and control.isVisible()
    assert page.table.empty_text == ''
    for size in ((1040, 820), (860, 640)):
        window.resize(*size)
        window.history_panel.set_collapsed(size[0] == 1040)
        qtbot.wait(30)
        viewport = page.table.viewport()
        assert viewport.rect().contains(notice.geometry())
        assert (viewport.rect().center() - notice.geometry().center()).manhattanLength() <= 2
        assert notice.title.geometry().bottom() < notice.instruction.geometry().top()
        assert notice.instruction.geometry().right() < notice.refresh_icon.geometry().left()
        assert notice.refresh_icon.geometry().right() < notice.refresh_link.geometry().left()
        assert notice.refresh_link.geometry().right() < notice.suffix.geometry().left()
    qtbot.mouseMove(notice.refresh_link)
    window.grab().save(str(tmp_path / 'completion-notice.png'))


@pytest.mark.parametrize('total_seconds,effective_seconds,total,effective', [
    (0, 0, '0 m', '0 m'),
    (3569, 59, '59 m', '0 m'),
    (3570, 3599, '1 h 0 m', '59 m'),
    (6000, 3600, '1 h 40 m', '1 h 0 m'),
    (90000, 89999, '25 h 0 m', '24 h 59 m'),
    (-1, 60, '—', '1 m'),
])
def test_notice_formats_hours_without_changing_rounding(window, repo, monkeypatch,
                                                      total_seconds, effective_seconds, total, effective):
    page, _, _ = finish(window, repo, monkeypatch)
    history = page.timer.history
    end = datetime.fromisoformat(history.started_at) + timedelta(seconds=total_seconds)
    history = replace(history, ended_at=end.isoformat(), duration_seconds=effective_seconds)
    page.completion_notice.set_history(history)
    assert f'总时长 {total}，有效用时 {effective}，' in page.completion_notice.title.text()


def test_completed_notice_survives_refresh_and_tab_switch_but_yields_to_completed_words(window, repo, monkeypatch):
    page, _, _ = finish(window, repo, monkeypatch)
    assert page.reload() and page.completion_notice.isVisible()
    window.set_filter('pending')
    assert not page.completion_notice.isVisible()
    window.set_filter('study17')
    assert page.completion_notice.isVisible()
    model = window.history_panel.model
    assert model.index(0, TOTAL_COLUMN).data() == '63 m'
    assert model.index(0, EFFICIENCY_COLUMN).data() == '76%'
    assert model.index(0, TOTAL_COLUMN + 1).data() == '47 m'
    page.toggle_completed(True)
    assert page.model.rowCount() and not page.completion_notice.isVisible()
    page.toggle_completed(False)
    assert page.completion_notice.isVisible() and not page.timer.running


@pytest.mark.parametrize('target', ['icon', 'link'])
def test_refresh_failure_keeps_completed_context_and_notice(window, repo, monkeypatch, qtbot, target):
    page, _, now = finish(window, repo, monkeypatch)
    errors = []
    page.error.connect(errors.append)
    now[0] = PM
    def fail(context, **kwargs):
        raise OSError('refresh failed')
    monkeypatch.setattr(repo, 'prepare_study', fail)
    qtbot.mouseClick(getattr(page.completion_notice, 'refresh_' + target), Qt.MouseButton.LeftButton)
    assert errors == ['refresh failed']
    assert page.context == AM and page.timer.history.status == 1
    assert page.completion_notice.isVisible()


def test_noninteractive_notice_text_does_not_refresh(window, repo, monkeypatch, qtbot):
    page, _, now = finish(window, repo, monkeypatch)
    now[0] = PM
    for control in (page.completion_notice.title, page.completion_notice.instruction, page.completion_notice.suffix):
        qtbot.mouseClick(control, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert page.context == AM and page.completion_notice.isVisible()

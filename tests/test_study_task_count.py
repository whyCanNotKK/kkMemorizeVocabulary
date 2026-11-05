from datetime import date, timedelta

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository


DAY = date(2026, 11, 3)
AM = StudyContext(DAY, False)
PM = StudyContext(DAY, True)


def seed(repo, count=3):
    return [repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义"), Sense("v.", "补充")],
                               [RelatedWord("child", [Sense("n.", "关联")])])) for i in range(count)]


def test_total_includes_completed_rounds_and_survives_restart(repo):
    entries = seed(repo)
    repo.prepare_study(AM)
    assert repo.study_task_total(AM) == repo.study_task_total(PM) == 3
    for entry in entries:
        repo.complete_study(entry.id, AM)
    assert repo.study_tasks(AM) == [] and repo.study_task_total(AM) == 3
    for entry in entries:
        repo.complete_study(entry.id, PM)
    assert repo.study_tasks(PM) == [] and repo.study_task_total(PM) == 3
    reopened = Repository(repo.path)
    try:
        assert reopened.study_task_total(AM) == reopened.study_task_total(PM) == 3
        tomorrow = StudyContext(DAY + timedelta(days=1), False)
        assert reopened.study_task_total(tomorrow) == 3  # Day 2 is due.
        for entry in entries:
            reopened.complete_study(entry.id, tomorrow)
            reopened.complete_study(entry.id, StudyContext(tomorrow.day, True))
        assert reopened.study_task_total(tomorrow) == 3
        assert reopened.study_task_total(StudyContext(DAY + timedelta(days=2), False)) == 0
    finally:
        reopened.close()


def test_total_counts_only_due_and_completed_today_including_final_stage(repo):
    entries = seed(repo, 4)
    repo.prepare_study(AM)
    with repo.connection:
        repo.connection.execute("UPDATE memorize_vocabulary_17day SET stage=15,last_completed_on=? WHERE word_id=?",
                                ((DAY - timedelta(days=8)).isoformat(), entries[0].id))
        repo.connection.execute("UPDATE memorize_vocabulary_17day SET stage=4,last_completed_on=? WHERE word_id=?",
                                ((DAY - timedelta(days=1)).isoformat(), entries[1].id))
        repo.connection.execute("UPDATE memorize_vocabulary_17day SET stage=0,last_completed_on=? WHERE word_id=?",
                                ((DAY - timedelta(days=1)).isoformat(), entries[2].id))
    assert repo.study_task_total(AM) == 2  # Final review plus one new word.
    repo.complete_study(entries[0].id, AM)
    repo.complete_study(entries[0].id, PM)
    assert repo.study_task_total(PM) == 2  # Finishing day 15 must not shrink the total.
    repo.complete_study(entries[3].id, PM)  # Afternoon first still counts toward the round's total.
    assert repo.study_task_total(PM) == 2 and repo.study_tasks(PM) == []


def test_task_counter_shows_remaining_on_right(window, repo, qtbot, monkeypatch, tmp_path):
    entries = seed(repo)
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: AM))
    window.set_filter("study17")
    page = window.study_page
    qtbot.wait(20)
    assert page.period_label.text() == AM.label
    assert page.new_count.accessibleName() == '新词：剩余3/3'
    assert page.review_count.accessibleName() == '复习：剩余0/0'
    assert page.new_count.alignment() & Qt.AlignmentFlag.AlignRight
    assert page.refresh_button.geometry().right() < page.period_label.geometry().left()
    assert page.elapsed_label.geometry().right() < page.timer_button.geometry().left()
    assert page.new_count.geometry().right() < page.completed_button.geometry().left()
    assert page.completed_button.geometry().right() >= page.width() - 20
    page.complete(entries[0].id, "study")
    assert page.new_count.accessibleName() == '新词：剩余2/3'
    window.grab().save(str(tmp_path / "study-task-counter.png"))
    assert page.reload() and page.new_count.accessibleName() == '新词：剩余2/3'
    window.set_filter("pending")
    window.set_filter("study17")
    assert page.new_count.accessibleName() == '新词：剩余2/3'
    for entry in entries[1:]:
        page.complete(entry.id, "study")
    assert page.new_count.accessibleName() == '新词：剩余0/3'
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: PM))
    assert page.reload() and page.new_count.accessibleName() == '新词：剩余3/3'
    for entry in entries:
        page.complete(entry.id, "study")
    assert page.new_count.accessibleName() == '新词：剩余0/3'
    assert page.reload() and page.new_count.accessibleName() == '新词：剩余0/3'


def test_counter_empty_and_failures_preserve_previous_value(window, repo, monkeypatch):
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: AM))
    window.set_filter("study17")
    page = window.study_page
    assert page.new_count.accessibleName() == '新词：剩余0/0'
    assert page.review_count.accessibleName() == '复习：剩余0/0'
    entry = seed(repo, 1)[0]
    assert page.load(StudyContext(DAY + timedelta(days=1), False), generate=True)
    before = page.new_count.text()

    def fail(*args, **kwargs):
        raise OSError("locked")

    monkeypatch.setattr(repo, "complete_study", fail)
    page.complete(entry.id, "study")
    assert page.new_count.text() == before
    monkeypatch.setattr(repo, "study_task_total", fail)
    assert not page.load(AM)
    assert page.new_count.text() == before and len(page.tasks) == 1

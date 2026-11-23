from datetime import date

import pytest

from vocabulary.domain import Sense, WordEntry
from vocabulary.learning import StudyContext, StudyDrawSettings
from vocabulary.storage import Repository


AM = StudyContext(date(2026, 11, 22), False)


def add_words(repo, count, important):
    return {repo.save(WordEntry(f'{important}-{index}', [Sense('n.', 'meaning')],
                               important=important)).id for index in range(count)}


@pytest.mark.parametrize('important_count,normal_count', [
    (60, 70), (40, 70), (35, 70), (0, 70), (60, 0), (4, 3), (0, 0),
])
def test_draw_prioritizes_eighty_percent_and_fills_available_words(repo, important_count, normal_count):
    repo.write_study_draw_settings(StudyDrawSettings(10, 5))
    important = add_words(repo, important_count, True)
    normal = add_words(repo, normal_count, False)
    tasks = repo.prepare_study(AM)
    selected = {task.entry.id for task in tasks}
    assert len(tasks) == len(selected) == min(50, important_count + normal_count)
    assert selected <= important | normal
    assert len(selected & important) >= min(40, important_count)
    if important_count <= 40:
        assert important <= selected
    assert [task.group_number for task in tasks] == [index // 10 + 1 for index in range(len(tasks))]
    # Today's batch and grouping stay fixed across refresh and restart.
    reopened = Repository(repo.path)
    try:
        assert reopened.prepare_study(AM) == tasks
    finally:
        reopened.close()


@pytest.mark.parametrize('reverse,expected_important', [(False, 45), (True, 40)])
def test_fill_can_select_remaining_important_and_normal_words(repo, monkeypatch, reverse, expected_important):
    repo.write_study_draw_settings(StudyDrawSettings(10, 5))
    important = add_words(repo, 45, True)
    normal = add_words(repo, 80, False)
    # Exercise both ends of the random choices without a probabilistic assertion.
    monkeypatch.setattr('vocabulary.storage.random.sample',
                        lambda population, count: sorted(population, reverse=reverse)[:count])
    selected = {task.entry.id for task in repo.prepare_study(AM)}
    assert len(selected) == 50
    assert len(selected & important) == expected_important
    assert len(selected & normal) == 50 - expected_important


@pytest.mark.parametrize('daily_count', [1, 3, 7])
def test_fractional_priority_quota_rounds_down(repo, monkeypatch, daily_count):
    repo.write_study_draw_settings(StudyDrawSettings(daily_count, 1))
    important = add_words(repo, 10, True)
    add_words(repo, 10, False)
    monkeypatch.setattr('vocabulary.storage.random.sample',
                        lambda population, count: sorted(population, reverse=True)[:count])
    selected = {task.entry.id for task in repo.prepare_study(AM)}
    assert len(selected) == daily_count
    assert len(selected & important) == daily_count * 4 // 5


def test_priority_does_not_reenroll_participants_or_learned_words(repo):
    repo.write_study_draw_settings(StudyDrawSettings(10, 5))
    excluded = [repo.save(WordEntry(text, [Sense('n.', 'meaning')], important=True, learned=learned))
                for text, learned in [('learned', True), ('finished', False), ('ongoing', False)]]
    with repo.connection:
        repo.connection.executemany('''INSERT INTO memorize_vocabulary_17day
            (word_id,group_number,stage,last_completed_on) VALUES (?,1,?,?)''',
            [(excluded[1].id, 0, AM.day.isoformat()), (excluded[2].id, 15, AM.day.isoformat())])
    important = add_words(repo, 35, True)
    normal = add_words(repo, 70, False)
    selected = {task.entry.id for task in repo.prepare_study(AM)}
    assert len(selected) == 50
    assert important <= selected <= important | normal
    assert not selected & {entry.id for entry in excluded}

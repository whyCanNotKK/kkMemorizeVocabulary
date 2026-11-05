from datetime import date, timedelta
import json
import sqlite3

import pytest

from vocabulary import migration
from vocabulary.domain import Sense, ValidationError, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_timer import StudyTimer
from vocabulary.study_transfer import parse_study_backup, serialize_study_backup


AM = StudyContext(date(2026, 11, 5), False)


@pytest.mark.parametrize('nanoseconds,seconds', [
    (0, 0), (499_999_999, 0), (500_000_000, 1),
    (1_499_999_999, 1), (1_500_000_000, 2), (2_500_000_000, 3),
])
def test_timer_persists_half_up_integer_seconds(qapp, repo, nanoseconds, seconds):
    repo.save(WordEntry('rounding', [Sense('n.', '取整')]))
    repo.prepare_study(AM)
    ticks = [0]
    timer = StudyTimer(repo, clock=lambda: ticks[0])
    try:
        timer.bind(AM)
        timer.start()
        ticks[0] = nanoseconds
        timer.pause()
        assert timer.duration_seconds() == seconds
        history = repo.study_task_history(AM)
        assert type(history.duration_seconds) is int and history.duration_seconds == seconds
        assert repo.connection.execute('SELECT typeof(duration_seconds) FROM study_task_history').fetchone()[0] == 'integer'
        assert 'duration_ms' not in [row['name'] for row in repo.connection.execute('PRAGMA table_info(study_task_history)')]
    finally:
        timer.dispose()


def old_database(tmp_path):
    path = tmp_path / 'v6.sqlite3'
    raw = sqlite3.connect(path)
    for name in migration.MIGRATIONS[:6]:
        raw.executescript(migration.read_migration(name))
    milliseconds = [0, 499, 500, 1499, 1500, 2500, 9223372036854775807]
    for index, duration in enumerate(milliseconds):
        raw.execute('''INSERT INTO study_task_history
            (id,task_date,afternoon,started_at,ended_at,duration_ms,status,new_total,review_total,new_done,review_done)
            VALUES (?,?,0,?,?,?,1,2,3,2,3)''',
            (100 + index, (AM.day + timedelta(days=index)).isoformat(),
             '2026-11-05T08:00:00+08:00', '2026-11-05T08:01:00+08:00', duration))
    raw.execute('PRAGMA user_version=6')
    raw.commit()
    before = raw.execute('SELECT * FROM study_task_history ORDER BY id').fetchall()
    raw.close()
    return path, before


def test_v6_migration_rounds_existing_records_and_reopen_does_not_convert_again(tmp_path):
    path, before = old_database(tmp_path)
    for _ in range(2):
        upgraded = Repository(path)
        try:
            after = upgraded.connection.execute('SELECT * FROM study_task_history ORDER BY id').fetchall()
            for original, converted in zip(before, after):
                assert tuple(converted) == (*original[:5], original[5] // 1000 + int(original[5] % 1000 >= 500), *original[6:])
            assert upgraded.connection.execute('PRAGMA user_version').fetchone()[0] == 10
        finally:
            upgraded.close()


def test_failed_seconds_migration_rolls_back_original_v6_data_and_schema(tmp_path, monkeypatch):
    path, before = old_database(tmp_path)
    original = migration.read_migration
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'read_migration', lambda name: original(name) + '\nSELECT * FROM missing_table;')
        with pytest.raises(sqlite3.OperationalError):
            Repository(path)
    raw = sqlite3.connect(path)
    try:
        assert raw.execute('PRAGMA user_version').fetchone()[0] == 6
        assert raw.execute('SELECT * FROM study_task_history ORDER BY id').fetchall() == before
        assert raw.execute("SELECT name FROM sqlite_master WHERE name='study_task_history_seconds'").fetchone() is None
    finally:
        raw.close()


@pytest.mark.parametrize('value', [True, -1, 1.5, '2'])
def test_seconds_json_rejects_noninteger_or_negative_duration(repo, value):
    repo.save(WordEntry('word', [Sense('n.', '单词')]))
    repo.prepare_study(AM)
    repo.start_study_task(AM)
    document = json.loads(serialize_study_backup(repo.study_backup()))
    document['task_history'][0]['duration_seconds'] = value
    with pytest.raises(ValidationError):
        parse_study_backup(json.dumps(document))
    with pytest.raises(ValueError):
        repo.save_study_duration(AM, value)

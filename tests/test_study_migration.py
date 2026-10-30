"""Real v3/v4 schemas verify one-way migration, rather than relabeling a new table."""
from datetime import date, timedelta
import sqlite3

import pytest

from vocabulary.domain import Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import RECORD_FIELDS, parse_study_backup, serialize_study_backup


DAY = date(2026, 10, 30)
COLUMNS = ['word_id', 'group_number', 'stage', 'morning_done', 'afternoon_done', 'last_completed_on']


def legacy_database(tmp_path, version, dates=('2026-10-28', '2026-10-30', '2026-10-29')):
    path = tmp_path / f'legacy-v{version}.sqlite3'
    repo = Repository(path)
    words = [repo.save(WordEntry(f'word-{i}', [Sense('n.', f'释义{i}')], learned=i == 5)) for i in range(6)]
    repo.write_shortcuts({'save': 'Alt+S'})
    repo.close()
    connection = sqlite3.connect(path)
    connection.executescript('''
        DROP TABLE memorize_vocabulary_17day;
        CREATE TABLE memorize_vocabulary_17day (
            word_id INTEGER PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
            group_number INTEGER NOT NULL, position INTEGER NOT NULL, enrolled_on TEXT NOT NULL,
            stage INTEGER NOT NULL, next_due_on TEXT NOT NULL,
            morning_done INTEGER NOT NULL DEFAULT 0, afternoon_done INTEGER NOT NULL DEFAULT 0,
            last_completed_on TEXT, completed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(group_number,position));
        CREATE INDEX study_due ON memorize_vocabulary_17day(completed,next_due_on);
    ''')
    rows = [
        (words[0].id, 7, 3, '2026-10-20', 1, '2026-10-20', 1, 0, None, 0),
        (words[1].id, 7, 1, '2026-10-20', 1, '2026-10-20', 0, 1, None, 0),
        (words[2].id, 7, 2, '2026-10-20', 2, '2026-10-31', 0, 0, DAY.isoformat(), 0),
        (words[3].id, 8, 1, '2026-10-21', 4, '2026-11-01', 1, 0, DAY.isoformat(), 0),
        (words[4].id, 8, 2, '2026-10-21', 15, '2026-11-07', 0, 0, DAY.isoformat(), 0),
        (words[5].id, 8, 3, '2026-10-21', 15, '2026-10-30', 0, 0, DAY.isoformat(), 1),
    ]
    connection.executemany('INSERT INTO memorize_vocabulary_17day VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    connection.execute("INSERT OR REPLACE INTO settings VALUES ('study17.group_counter','20')")
    if version == 3:
        connection.executemany("INSERT INTO settings VALUES (?, '1')", [(f'study17.generated.{day}',) for day in dates])
    else:
        connection.execute("INSERT INTO settings VALUES ('study_last_add_new_vocabulary',?)", (DAY.isoformat(),))
    connection.execute(f'PRAGMA user_version={version}')
    connection.commit()
    connection.close()
    return path, words, rows


@pytest.mark.parametrize('version,dates', [(3, ()), (3, ('2026-10-29', '2026-10-30', '2026-10-28')), (4, ())])
def test_real_legacy_upgrade_preserves_progress_settings_and_drops_old_columns(tmp_path, version, dates):
    path, words, before = legacy_database(tmp_path, version, dates)
    repo = Repository(path)
    try:
        assert repo.connection.execute('PRAGMA user_version').fetchone()[0] == 5
        assert [row['name'] for row in repo.connection.execute('PRAGMA table_info(memorize_vocabulary_17day)')] == COLUMNS
        rows = repo.connection.execute('SELECT * FROM memorize_vocabulary_17day ORDER BY word_id').fetchall()
        assert [tuple(row) for row in rows] == [
            (old[0], old[1], 0 if old[9] else old[4], old[6], old[7], old[8]) for old in before]
        assert repo.list_entries() == words
        assert repo.shortcuts()['save'] == 'Alt+S'
        assert repo.study_backup().group_counter == 20
        assert repo.study_backup().study_last_add_new_vocabulary == (
            DAY.isoformat() if version == 4 else max(dates, default=None))
        assert repo.connection.execute("SELECT key FROM settings WHERE key GLOB 'study17.generated.*'").fetchall() == []
        assert [row['name'] for row in repo.connection.execute('PRAGMA index_info(study_due)')] == ['stage', 'last_completed_on']
        assert repo.connection.execute('PRAGMA foreign_key_check').fetchall() == []
        morning = repo.study_tasks(StudyContext(DAY, False))
        afternoon = repo.study_tasks(StudyContext(DAY, True))
        assert [task.entry.id for task in morning] == [words[1].id]
        assert [task.entry.id for task in afternoon] == [words[0].id]
        repo.complete_study(words[0].id, StudyContext(DAY, True))
        assert [task.entry.id for task in repo.study_tasks(StudyContext(DAY + timedelta(days=1), False))] == [
            words[0].id, words[1].id, words[2].id]
        snapshot = repo.study_backup()
    finally:
        repo.close()
    reopened = Repository(path)
    try:
        assert reopened.study_backup() == snapshot
        assert reopened.connection.execute('PRAGMA user_version').fetchone()[0] == 5
    finally:
        reopened.close()


@pytest.mark.parametrize('failure_at', ['CREATE INDEX study_due', 'PRAGMA user_version=5'])
def test_migration_failure_rolls_back_schema_records_settings_and_version(tmp_path, monkeypatch, failure_at):
    path, _, before = legacy_database(tmp_path, 4)
    original_connect = sqlite3.connect
    connections = []
    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if failure_at in sql:
                raise sqlite3.OperationalError('migration failed')
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=FailingConnection)
        connections.append(connection)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connect)
        with pytest.raises(sqlite3.OperationalError, match='migration failed'):
            Repository(path)
    for connection in connections:
        connection.close()
    raw = original_connect(path)
    try:
        assert raw.execute('PRAGMA user_version').fetchone()[0] == 4
        assert raw.execute('SELECT * FROM memorize_vocabulary_17day ORDER BY word_id').fetchall() == before
        assert raw.execute("SELECT count(*) FROM settings WHERE key GLOB 'study17.generated.*'").fetchone()[0] == 0
        assert raw.execute("SELECT value FROM settings WHERE key='study_last_add_new_vocabulary'").fetchone()[0] == DAY.isoformat()
        assert raw.execute("SELECT name FROM sqlite_master WHERE name='memorize_vocabulary_17day_v5'").fetchone() is None
    finally:
        raw.close()
    reopened = Repository(path)
    reopened.close()


def test_new_schema_sorting_and_stage_zero_backup(repo):
    words = [repo.save(WordEntry(f'word-{i}', [Sense('n.', '释义')])) for i in range(12)]
    repo.prepare_study(StudyContext(DAY, False))
    tasks = repo.study_tasks(StudyContext(DAY, False))
    assert [(task.group_number, task.entry.id) for task in tasks] == sorted(
        (task.group_number, task.entry.id) for task in tasks)
    assert [row['name'] for row in repo.connection.execute('PRAGMA table_info(memorize_vocabulary_17day)')] == COLUMNS
    target = words[0]
    for offset in (0, 1, 3, 6, 14):
        context_day = DAY + timedelta(days=offset)
        repo.complete_study(target.id, StudyContext(context_day, False))
        repo.complete_study(target.id, StudyContext(context_day, True))
    backup = parse_study_backup(serialize_study_backup(repo.study_backup()))
    assert backup.records[target.text]['stage'] == 0
    assert all(set(record) == set(RECORD_FIELDS) for record in backup.records.values())
    assert all(target.id != task.entry.id for task in repo.study_tasks(StudyContext(DAY + timedelta(days=100), False)))

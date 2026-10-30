from importlib.resources import files
from pathlib import Path
import sqlite3
import subprocess
import sys
import tomllib
from zipfile import ZipFile

import pytest

from scripts.verify_release import verify_migrations
from vocabulary import migration
from vocabulary.storage import Repository


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('start', range(6))
def test_each_start_version_runs_only_remaining_steps_and_commits_each(start, monkeypatch):
    connection = sqlite3.connect(':memory:')
    connection.execute('PRAGMA foreign_keys=ON')
    for index, name in enumerate(migration.MIGRATIONS[:start], 1):
        connection.executescript(migration.read_migration(name))
        connection.execute(f'PRAGMA user_version={index}')
    seen = []
    original_read = migration.read_migration
    def read(name):
        assert connection.in_transaction
        assert connection.execute('PRAGMA user_version').fetchone()[0] == start + len(seen)
        seen.append(name)
        return original_read(name)
    monkeypatch.setattr(migration, 'read_migration', read)
    try:
        migration.migrate_database(connection)
        assert seen == list(migration.MIGRATIONS[start:])
        assert not connection.in_transaction
        assert connection.execute('PRAGMA user_version').fetchone()[0] == migration.SCHEMA_VERSION
        assert [row[1] for row in connection.execute('PRAGMA table_info(memorize_vocabulary_17day)')] == [
            'word_id', 'group_number', 'stage', 'morning_done', 'afternoon_done', 'last_completed_on']
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        connection.close()


def test_failed_step_keeps_previously_committed_versions_and_can_resume(tmp_path, monkeypatch):
    connection = sqlite3.connect(tmp_path / 'resume.sqlite3')
    read = migration.read_migration
    def fail_third(name):
        script = read(name)
        if name == migration.MIGRATIONS[2]:
            script += "\nINSERT INTO settings VALUES ('partial','value');\nSELECT * FROM missing_table;"
        return script
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'read_migration', fail_third)
        with pytest.raises(sqlite3.OperationalError, match='missing_table'):
            migration.migrate_database(connection)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 2
    assert 'sort_order' in [row[1] for row in connection.execute('PRAGMA table_info(words)')]
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='memorize_vocabulary_17day'").fetchone() is None
    assert connection.execute("SELECT value FROM settings WHERE key='partial'").fetchone() is None
    migration.migrate_database(connection)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 5
    connection.close()


def test_complete_statement_parser_supports_comments_quoted_semicolons_and_triggers():
    script = """-- Chinese content and semicolons must survive: 中文;
        CREATE TABLE source(value TEXT); CREATE TABLE audit(value TEXT);
        INSERT INTO source VALUES ('before; after');
        CREATE TRIGGER changes AFTER UPDATE ON source BEGIN
            INSERT INTO audit VALUES ('trigger; value');
            INSERT INTO audit VALUES (NEW.value);
        END;
        /* Another ; comment */ UPDATE source SET value='更新;文字';
        -- Trailing comment without a semicolon
    """
    connection = sqlite3.connect(':memory:')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        for statement in migration.sql_statements(script):
            connection.execute(statement)
    assert connection.execute('SELECT * FROM audit').fetchall() == [('trigger; value',), ('更新;文字',)]
    connection.close()


def test_missing_script_never_advances_version(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, 'files', lambda package: tmp_path)
    connection = sqlite3.connect(':memory:')
    with pytest.raises(RuntimeError, match='001_initial.sql'):
        migration.migrate_database(connection)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 0
    assert connection.execute('SELECT name FROM sqlite_master').fetchall() == []
    assert not connection.in_transaction
    connection.close()


def test_higher_version_is_rejected_before_loading_scripts(monkeypatch):
    connection = sqlite3.connect(':memory:')
    connection.execute('PRAGMA user_version=6')
    monkeypatch.setattr(migration, 'read_migration', lambda name: pytest.fail('must not run SQL scripts'))
    with pytest.raises(RuntimeError):
        migration.migrate_database(connection)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 6
    assert not connection.in_transaction
    connection.close()


def test_repository_closes_connection_and_skips_business_initialization_on_failure(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    connections = []
    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', connect)
    def fail(name):
        raise RuntimeError('missing script')
    monkeypatch.setattr(migration, 'read_migration', fail)
    with pytest.raises(RuntimeError, match='missing script'):
        Repository(tmp_path / 'failed.sqlite3')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connections[0].execute('SELECT 1')


def test_sql_resources_work_from_an_installed_zip_package(tmp_path):
    package = tmp_path / 'installed.zip'
    with ZipFile(package, 'w') as archive:
        archive.writestr('vocabulary/__init__.py', '')
        archive.writestr('vocabulary/migration.py', (ROOT / 'vocabulary/migration.py').read_bytes())
        for name in migration.MIGRATIONS:
            archive.writestr(f'vocabulary/migrations/{name}', migration.read_migration(name))
    result = subprocess.run([sys.executable, '-I', '-c',
        'import sys,sqlite3; sys.path.insert(0,sys.argv[1]); '
        'from vocabulary.migration import migrate_database; '
        'connection=sqlite3.connect(":memory:"); migrate_database(connection); '
        'print(connection.execute("PRAGMA user_version").fetchone()[0])', str(package)],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '5'
    configuration = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    assert 'migrations/*.sql' in configuration['tool']['setuptools']['package-data']['vocabulary']


@pytest.mark.parametrize('damage', [None, 'missing', 'changed', 'extra'])
def test_release_verifier_checks_packaged_sql_collection_and_content(tmp_path, damage):
    directory = tmp_path / '_internal/vocabulary/migrations'
    directory.mkdir(parents=True)
    for name in migration.MIGRATIONS:
        (directory / name).write_bytes(files('vocabulary').joinpath('migrations', name).read_bytes())
    target = directory / migration.MIGRATIONS[0]
    if damage == 'missing':
        target.unlink()
    elif damage == 'changed':
        target.write_text('-- stale SQL', encoding='utf-8')
    elif damage == 'extra':
        (directory / 'obsolete.sql').write_text('SELECT 1;', encoding='utf-8')
    if damage is None:
        assert verify_migrations(tmp_path) == 5
    else:
        with pytest.raises(AssertionError):
            verify_migrations(tmp_path)

"""Run ordered UTF-8 SQL resources, committing one schema version at a time."""
from importlib.resources import files
import sqlite3


MIGRATIONS = (
    '001_initial.sql',
    '002_word_order.sql',
    '003_study.sql',
    '004_last_draw.sql',
    '005_six_field_study.sql',
)
SCHEMA_VERSION = len(MIGRATIONS)


def sql_statements(script):
    """Ask SQLite about boundaries so comments, quoted semicolons and triggers work."""
    pending = []
    for character in script:
        pending.append(character)
        if character == ';' and sqlite3.complete_statement(''.join(pending)):
            yield ''.join(pending).strip()
            pending.clear()
    # SQLite accepts a final statement without a semicolon, as well as trailing comments.
    remaining = ''.join(pending).strip()
    if remaining:
        yield remaining


def read_migration(filename):
    try:
        return files('vocabulary').joinpath('migrations', filename).read_text(encoding='utf-8')
    except FileNotFoundError as error:
        raise RuntimeError(f'数据库升级脚本缺失：{filename}，请使用完整的程序文件。') from error


def migrate_database(connection):
    if connection.in_transaction:
        raise RuntimeError('数据库升级必须在业务事务开始之前执行。')
    while True:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            # Read after obtaining the writer lock; another process may have just upgraded.
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version < 0 or version > SCHEMA_VERSION:
                raise RuntimeError('数据库由不支持的版本程序创建，请使用对应版本单词本打开。')
            if version == SCHEMA_VERSION:
                return
            script = read_migration(MIGRATIONS[version])
            for statement in sql_statements(script):
                connection.execute(statement)
            connection.execute(f'PRAGMA user_version={version + 1}')

from datetime import date
import json
import sqlite3

import pytest
from PySide6.QtCore import Qt

from vocabulary import migration
from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_study_backup, serialize_study_backup
from vocabulary.transfer import parse_entries, serialize_entries


def draft():
    return WordEntry('parent', [Sense('n.', ''), Sense('v.', '  \n\t')],
                     [RelatedWord('child', [Sense('adj.', '')])])


def test_blank_translations_keep_senses_and_roundtrip_all_formats(repo):
    saved = repo.save(draft())
    assert saved.senses == [Sense('n.', ''), Sense('v.', '')]
    assert saved.related[0].senses == [Sense('adj.', '')]
    parsed = parse_entries(serialize_entries([saved]))[0]
    assert parsed.senses == saved.senses and parsed.related == saved.related
    repo.prepare_study(StudyContext(date(2026, 11, 5), False))
    backup = repo.study_backup()
    assert serialize_study_backup(parse_study_backup(serialize_study_backup(backup))) == serialize_study_backup(backup)
    reopened = Repository(repo.path)
    try:
        assert reopened.get(saved.id) == saved
    finally:
        reopened.close()


@pytest.mark.parametrize('related', [False, True])
@pytest.mark.parametrize('field', ['text', 'pos'])
def test_names_and_pos_remain_required(repo, related, field):
    entry = draft()
    target = entry.related[0] if related else entry
    if field == 'text':
        target.text = ' '
    else:
        target.senses = [Sense('', '')]
    with pytest.raises(ValidationError):
        repo.save(entry)
    assert not repo.list_entries()


@pytest.mark.parametrize('related', [False, True])
@pytest.mark.parametrize('value', [None, 1, [], {}, False])
def test_json_optional_translation_still_requires_string(related, value):
    document = json.loads(serialize_entries([draft()]))
    target = document['words'][0]
    if related:
        target = target['related'][0]
    target['senses'][0]['translation'] = value
    with pytest.raises(ValidationError, match='翻译'):
        parse_entries(json.dumps(document))


def test_v8_upgrade_preserves_ids_data_and_rolls_back_failed_rebuild(tmp_path, monkeypatch):
    path = tmp_path / 'v8.sqlite3'
    raw = sqlite3.connect(path)
    for name in migration.MIGRATIONS[:8]:
        raw.executescript(migration.read_migration(name))
    raw.executescript('''
        INSERT INTO words(id,text,normalized_text,phonetic,sort_order) VALUES (7,'old','old','/old/',12);
        INSERT INTO related_words(id,word_id,text,position) VALUES (11,7,'child',0);
        INSERT INTO senses(id,word_id,pos,translation,position) VALUES (15,7,'n.','旧译文',0);
        INSERT INTO senses(id,related_id,pos,translation,position) VALUES (19,11,'v.','',0);
        INSERT INTO memorize_vocabulary_17day(word_id,group_number) VALUES (7,8);
        INSERT INTO settings VALUES ('study17.group_counter','8');
        PRAGMA user_version=8;
    ''')
    tables = ('words', 'related_words', 'senses', 'memorize_vocabulary_17day', 'settings')
    before = {table: raw.execute(f'SELECT * FROM {table}').fetchall() for table in tables}
    raw.close()
    read = migration.read_migration
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'read_migration', lambda name: read(name) + 'SELECT * FROM missing;')
        with pytest.raises(sqlite3.OperationalError):
            Repository(path)
    raw = sqlite3.connect(path)
    assert raw.execute('PRAGMA user_version').fetchone()[0] == 8
    for table in tables:
        assert raw.execute(f'SELECT * FROM {table}').fetchall() == before[table]
    assert not raw.execute("SELECT name FROM sqlite_master WHERE name='senses_new'").fetchall()
    raw.close()
    upgraded = Repository(path)
    try:
        assert upgraded.connection.execute('PRAGMA user_version').fetchone()[0] == 10
        for table in tables[:-1]:
            assert [tuple(row) for row in upgraded.connection.execute(f'SELECT * FROM {table}')] == before[table]
        assert upgraded.connection.execute('PRAGMA foreign_key_check').fetchall() == []
        with upgraded.connection:
            upgraded.connection.execute("INSERT INTO senses(word_id,pos,position) VALUES (7,'adv.',1)")
        assert upgraded.get(7).senses[-1] == Sense('adv.', '')
        with pytest.raises(sqlite3.IntegrityError), upgraded.connection:
            upgraded.connection.execute("INSERT INTO senses(word_id,pos,position) VALUES (7,' ',2)")
        upgraded.delete(7)
        assert not upgraded.connection.execute('SELECT * FROM senses').fetchall()
    finally:
        upgraded.close()


def test_form_add_and_edit_save_empty_translations(window, repo):
    window.reset_form(draft())
    assert window._save_form()
    saved = repo.list_entries()[0]
    assert saved.senses == [Sense('n.', ''), Sense('v.', '')]
    assert saved.related[0].senses == [Sense('adj.', '')]
    window.reset_form(saved)
    window.blocks[0].rows[0].translation.setPlainText('temporary')
    assert window._save_form()
    window.reset_form(repo.get(saved.id))
    window.blocks[0].rows[0].translation.clear()
    assert window._save_form()
    assert repo.get(saved.id).senses == saved.senses


@pytest.mark.parametrize('page', ['pending', 'all', 'learned', 'study17'])
@pytest.mark.parametrize('owner', [-1, 0])
def test_all_pages_can_clear_and_reedit_translation(window, repo, qtbot, monkeypatch, page, owner):
    entry = draft()
    entry.senses = [Sense('n.', 'before')]
    entry.related[0].senses = [Sense('adj.', 'before')]
    entry.learned = page == 'learned'
    saved = repo.save(entry)
    am = StudyContext(date(2026, 11, 5), False)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: am))
    window.set_filter(page)
    if page == 'study17':
        window.study_page.reveal(saved.id, True)
    else:
        window.reveal(saved.id, True)
    table = window.current_table()
    for value in ('', 'new translation', ''):
        index = table._entry_index(saved.id, owner=owner, column=3)
        table.setCurrentIndex(index)
        table.edit(index)
        editor = table.delegate.editor
        assert editor is not None
        editor.setPlainText(value)
        qtbot.keyClick(editor, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: table.delegate.editor is None)
        current = repo.get(saved.id)
        target = current if owner == -1 else current.related[0]
        assert target.senses == [Sense('n.' if owner == -1 else 'adj.', value)]


@pytest.mark.parametrize('page,owner,submit', [
    ('pending', -1, 'enter'), ('all', 0, 'focus'), ('learned', -1, 'shortcut'),
    ('study17', 0, 'keypad'),
])
def test_clearing_one_of_multiple_translations_removes_only_its_sense(
        window, repo, qtbot, monkeypatch, page, owner, submit):
    entry = draft()
    target = entry if owner == -1 else entry.related[0]
    target.senses = [Sense('n.', 'first'), Sense('v.', 'remove'), Sense('adj.', '')]
    entry.learned = page == 'learned'
    saved = repo.save(entry)
    am = StudyContext(date(2026, 11, 5), False)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: am))
    window.set_filter(page)
    table = window.current_table()
    table.revealRequested.emit(saved.id, True)
    table.edit(table._entry_index(saved.id, owner, 1, 3))
    editor = table.delegate.editor
    editor.setPlainText(' \n\t\n ')
    if submit == 'focus':
        window.search_box.setFocus()
    elif submit == 'shortcut':
        qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    elif submit == 'keypad':
        qtbot.keyClick(editor, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    else:
        qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: table.delegate.editor is None)
    expected = saved if owner == -1 else saved.related[owner]
    expected.senses.pop(1)
    current = repo.get(saved.id)
    assert current == saved
    assert [row.sense for row in table.model().rows if row.owner == owner] == [0, 1]


def test_failed_or_stale_sense_removal_preserves_input(window, repo, qtbot):
    saved = repo.save(draft())
    window.reveal(saved.id, True)
    table = window.table
    table.edit(table._entry_index(saved.id, -1, 1, 3))
    editor = table.delegate.editor
    editor.setPlainText(' \n ')
    with repo.connection:
        repo.connection.execute("""CREATE TRIGGER reject_removal BEFORE DELETE ON senses
            BEGIN SELECT RAISE(ABORT, 'delete failed'); END""")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert table.delegate.editor is editor and editor.toPlainText() == ' \n '
    assert repo.get(saved.id) == saved
    with repo.connection:
        repo.connection.execute("DROP TRIGGER reject_removal")
    repo.move_sense(saved.id, -1, 0, 1)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert table.delegate.editor is editor
    assert repo.get(saved.id).senses == saved.senses[::-1]

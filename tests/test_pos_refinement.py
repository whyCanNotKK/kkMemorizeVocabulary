from datetime import date
import json
import sqlite3

import pytest
from PySide6.QtCore import Qt

from vocabulary import migration
from vocabulary.domain import POS_OPTIONS, RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.pos_picker import PosPicker
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_study_backup, serialize_study_backup
from vocabulary.transfer import parse_entries, serialize_entries
from vocabulary.widgets import PosCombo


@pytest.mark.parametrize('key', [Qt.Key.Key_Escape, Qt.Key.Key_Backspace])
@pytest.mark.parametrize('popup', [False, True])
@pytest.mark.parametrize('related', [False, True])
@pytest.mark.parametrize('editing', [False, True])
def test_clear_pos_stays_in_form_and_remains_required(window, repo, qtbot, key, popup, related, editing):
    entry = WordEntry('word', [Sense('n.', '')], [RelatedWord('child', [Sense('v.', '')])])
    if editing:
        entry = repo.save(entry)
    window.reset_form(entry)
    combo = window.blocks[int(related)].rows[0].pos
    combo.setFocus()
    if popup:
        combo.showPopup()
    qtbot.keyClick(combo.view() if popup else combo, key)
    assert combo.currentIndex() == -1 and combo.currentData() is None
    assert combo.hasFocus() and not combo.view().isVisible()
    assert window.edit_id == (entry.id if editing else None)
    assert not window._save_form()
    if editing:
        assert repo.get(entry.id) == entry
    else:
        assert not repo.list_entries()
    qtbot.keyClick(combo, Qt.Key.Key_2)
    assert combo.currentData() == 'v.'
    assert window._save_form()


def test_else_is_final_option_in_both_pickers_and_digit_shortcut(qtbot):
    assert POS_OPTIONS[-1] == ('else', '其他')
    combo = PosCombo()
    picker = PosPicker()
    qtbot.addWidget(combo)
    qtbot.addWidget(picker)
    combo.show()
    assert combo.count() == picker.count() == 9
    assert combo.itemText(8) == '9. else    其他'
    assert 'else' in picker.itemText(8)
    qtbot.keyClick(combo, Qt.Key.Key_9)
    assert combo.currentData() == 'else'
    picker.set_pos('else')
    assert picker.currentData() == 'else'


def test_legacy_chinese_pos_normalizes_in_word_and_study_import(repo):
    saved = repo.save(WordEntry('word', [Sense('else', '')],
                               [RelatedWord('child', [Sense('else', 'child meaning')])]))
    document = json.loads(serialize_entries([saved]))
    document['words'][0]['senses'][0]['pos'] = '其他'
    document['words'][0]['related'][0]['senses'][0]['pos'] = '其他'
    imported = parse_entries(json.dumps(document))[0]
    assert imported.senses == saved.senses and imported.related == saved.related
    repo.prepare_study(StudyContext(date(2026, 11, 5), False))
    backup = json.loads(serialize_study_backup(repo.study_backup()))
    backup['words'] = document['words']
    parsed = parse_study_backup(json.dumps(backup))
    assert parsed.words[0].senses == saved.senses and parsed.words[0].related == saved.related
    legacy = WordEntry('legacy', [Sense('其他', '')], [RelatedWord('child', [Sense('其他', '')])])
    normalized = repo.save(legacy)
    assert normalized.senses == normalized.related[0].senses == [Sense('else', '')]


def test_v9_else_migration_preserves_data_and_rolls_back(repo, monkeypatch):
    saved = repo.save(WordEntry('word', [Sense('else', ''), Sense('v.', 'meaning')],
                               [RelatedWord('child', [Sense('else', 'child meaning')])]))
    context = StudyContext(date(2026, 11, 5), False)
    repo.prepare_study(context)
    repo.start_study_task(context)
    with repo.connection:
        repo.connection.execute("UPDATE senses SET pos='其他' WHERE pos='else'")
        repo.connection.execute('PRAGMA user_version=9')
    tables = ('words', 'related_words', 'senses', 'settings', 'memorize_vocabulary_17day', 'study_task_history')
    before = {table: [tuple(row) for row in repo.connection.execute(f'SELECT * FROM {table}')]
              for table in tables}
    read = migration.read_migration
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'read_migration', lambda name: read(name) + 'SELECT * FROM missing;')
        with pytest.raises(sqlite3.OperationalError):
            Repository(repo.path)
    assert repo.connection.execute('PRAGMA user_version').fetchone()[0] == 9
    for table in tables:
        assert [tuple(row) for row in repo.connection.execute(f'SELECT * FROM {table}')] == before[table]
    upgraded = Repository(repo.path)
    try:
        assert upgraded.get(saved.id) == saved
        assert upgraded.connection.execute('PRAGMA user_version').fetchone()[0] == migration.SCHEMA_VERSION
        for table in tables:
            if table != 'senses':
                assert [tuple(row) for row in upgraded.connection.execute(f'SELECT * FROM {table}')] == before[table]
        after = [tuple(row) for row in upgraded.connection.execute('SELECT * FROM senses')]
        expected = [tuple('else' if column == 3 and value == '其他' else value
                          for column, value in enumerate(row)) for row in before['senses']]
        assert after == expected
    finally:
        upgraded.close()

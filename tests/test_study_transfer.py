from copy import deepcopy
from datetime import date, timedelta
import json
import sqlite3

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import (parse_study_backup, read_study_backup,
                                       serialize_study_backup, write_study_backup)
from vocabulary.transfer import parse_entries, serialize_entries


DAY = date(2026, 10, 30)
AM, PM = StudyContext(DAY, False), StudyContext(DAY, True)


def add(repo, text='Alpha', translation='中文\n第二行'):
    return repo.save(WordEntry(text, [Sense('n.', translation), Sense('v.', '动词')],
                              [RelatedWord('child', [Sense('n.', '关联词')])]))


@pytest.fixture
def source(tmp_path):
    repo = Repository(tmp_path / 'source.sqlite3')
    word = add(repo)
    repo.prepare_study(AM)
    repo.complete_study(word.id, AM)
    yield repo
    repo.close()


@pytest.fixture
def document(source):
    return json.loads(serialize_study_backup(source.study_backup()))


def test_export_only_participants_including_future_done_and_completed(repo):
    entries = [add(repo, f'word-{i}') for i in range(35)]
    tasks = repo.prepare_study(AM)
    for offset in (0, 1, 3, 6, 14):
        day = DAY + timedelta(days=offset)
        repo.complete_study(tasks[0].entry.id, StudyContext(day, False))
        repo.complete_study(tasks[0].entry.id, StudyContext(day, True))
    repo.complete_study(tasks[1].entry.id, AM)
    repo.complete_study(tasks[1].entry.id, PM)
    repo.complete_study(tasks[2].entry.id, PM)
    before = repo.study_backup()
    content = serialize_study_backup(before)
    exported = parse_study_backup(content)
    assert len(exported.words) == len(exported.records) == 30
    assert len(repo.list_entries()) == len(entries) == 35
    assert {entry.text.casefold() for entry in exported.words} == set(exported.records)
    assert any(record['completed'] for record in exported.records.values())
    assert any(record['next_due_on'] > DAY.isoformat() for record in exported.records.values())
    assert any(record['afternoon_done'] for record in exported.records.values())
    assert repo.study_backup() == before
    assert 'word_id' not in content and '"id"' not in content
    assert all(entry.id is None for entry in exported.words)
    assert exported.words[0].senses == before.words[0].senses
    assert exported.words[0].related == before.words[0].related


def test_read_export_is_one_consistent_snapshot(repo):
    entry = add(repo)
    repo.prepare_study(AM)
    other = Repository(repo.path)
    original_get = repo.get
    changed = False
    def concurrent_get(word_id):
        nonlocal changed
        if not changed:
            changed = True
            other.set_flag(word_id, 'learned', True)
        return original_get(word_id)
    repo.get = concurrent_get
    try:
        assert not repo.study_backup().words[0].learned
        assert original_get(entry.id).learned
    finally:
        other.close()


def test_cross_database_roundtrip_and_repeat_replace_maps_final_ids(source, repo):
    unrelated = add(repo, 'unrelated')
    current = add(repo, '  ALPHA  ', '保留本机')
    repo.prepare_study(PM)
    repo.write_shortcuts({'save': 'Alt+S'})
    backup = parse_study_backup(serialize_study_backup(source.study_backup()))
    result = repo.import_entries(backup.words)
    assert len(result.conflicts) == 1
    repo.replace_study_progress(backup)
    snapshot = repo.study_backup()
    assert snapshot.records == backup.records
    assert snapshot.words[0].id == current.id != backup.words[0].id
    assert snapshot.words[0].senses[0].translation == '保留本机'
    assert repo.get(unrelated.id)
    adopted = repo.replace_imports(result.conflicts)['alpha']
    repo.replace_study_progress(backup)
    assert repo.study_backup().words[0].id == adopted.id != current.id
    assert repo.study_backup().records == backup.records
    assert repo.shortcuts()['save'] == 'Alt+S'
    assert repo.prepare_study(AM) == []  # morning completion and the generated-date marker survived
    assert len(repo.prepare_study(PM)) == 1
    reopened = Repository(repo.path)
    try:
        assert reopened.study_backup() == repo.study_backup()
    finally:
        reopened.close()


def test_empty_snapshot_clears_progress_and_replaces_markers_but_keeps_words(source, repo):
    empty = repo.study_backup()
    before_words = source.list_entries()
    source.replace_study_progress(empty)
    assert not source.has_study_progress()
    assert source.list_entries() == before_words
    assert source.study_backup().generated_dates == []


@pytest.mark.parametrize('corruption', [
    lambda d: d.update(version=2),
    lambda d: d.update(format='kkMemorizeVocabulary'),
    lambda d: d.update(word_id=1),
    lambda d: d['words'].append(deepcopy(d['words'][0])),
    lambda d: d['words'][0].update(text='Other'),
    lambda d: d.update(study_records=[]),
    lambda d: d['study_records']['alpha'].update(word_id=1),
    lambda d: d['study_records']['alpha'].update(group_number=True),
    lambda d: d['study_records']['alpha'].update(position=11),
    lambda d: d['study_records']['alpha'].update(stage=3),
    lambda d: d['study_records']['alpha'].update(morning_done=1),
    lambda d: d['study_records']['alpha'].update(afternoon_done=True),
    lambda d: d['study_records']['alpha'].update(enrolled_on='2026-02-30'),
    lambda d: d['study_records']['alpha'].update(next_due_on='20261030'),
    lambda d: d['study_records']['alpha'].update(last_completed_on='2026-10-29'),
    lambda d: d['study_records']['alpha'].update(completed=True),
    lambda d: d['study_settings'].update(generated_dates=[]),
    lambda d: d['study_settings'].update(generated_dates=['2026-10-30', '2026-10-30']),
    lambda d: d['study_settings'].update(group_counter=0),
])
def test_invalid_backup_rejected_before_import(document, corruption):
    corruption(document)
    with pytest.raises(ValidationError):
        parse_study_backup(json.dumps(document))


def test_duplicate_json_keys_group_positions_and_bad_stage_dates(document):
    content = json.dumps(document)
    with pytest.raises(ValidationError):
        parse_study_backup(content.replace('"version": 1', '"version": 1, "version": 1'))
    document['words'].append(dict(deepcopy(document['words'][0]), text='Beta'))
    document['study_records']['beta'] = deepcopy(document['study_records']['alpha'])
    with pytest.raises(ValidationError, match='位置'):
        parse_study_backup(json.dumps(document))
    document['study_records']['beta']['position'] = 2
    document['study_records']['beta'].update(stage=2, last_completed_on='2026-10-30', next_due_on='2026-11-02')
    with pytest.raises(ValidationError, match='不一致'):
        parse_study_backup(json.dumps(document))


def test_formats_are_separate_and_bom_roundtrip(source, tmp_path):
    backup = source.study_backup()
    with pytest.raises(ValidationError):
        parse_entries(serialize_study_backup(backup))
    with pytest.raises(ValidationError):
        parse_study_backup(serialize_entries(backup.words))
    path = tmp_path / '中文进度.json'
    path.write_text(serialize_study_backup(backup), encoding='utf-8-sig')
    assert read_study_backup(path).records == backup.records


def test_atomic_export_keeps_original_on_failure(source, tmp_path, monkeypatch):
    path = tmp_path / 'backup.json'
    path.write_text('original', encoding='utf-8')
    def fail(*args):
        raise PermissionError('replace failed')
    monkeypatch.setattr('vocabulary.transfer.os.replace', fail)
    with pytest.raises(PermissionError):
        write_study_backup(path, source.study_backup())
    assert path.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.glob('backup.json.*.tmp')) == []


def test_progress_replacement_failure_preserves_old_settings_and_saved_words(source, repo):
    add(repo, 'local')
    repo.prepare_study(AM)
    before = repo.study_backup()
    incoming = source.study_backup()
    repo.import_entries(incoming.words)
    repo.connection.execute('''CREATE TRIGGER fail_restore BEFORE INSERT ON settings
        WHEN NEW.key='study17.group_counter' BEGIN SELECT RAISE(ABORT,'restore failed'); END''')
    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_study_progress(incoming)
    assert repo.study_backup() == before
    assert len(repo.list_entries()) == 2
    assert repo.shortcuts()


def test_missing_word_resolution_leaves_progress_unchanged(source, repo):
    add(repo, 'local')
    repo.prepare_study(AM)
    before = repo.study_backup()
    with pytest.raises(ValueError, match='尚未导入'):
        repo.replace_study_progress(source.study_backup())
    assert repo.study_backup() == before


def enter(window, monkeypatch, context=PM):
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: context))
    window.set_filter('study17')
    return window.study_page


def import_ui(window, monkeypatch, tmp_path, backup, mode='full'):
    path = tmp_path / 'study.json'
    write_study_backup(path, backup)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(window, 'choose_study_import_mode', lambda: mode)
    info, errors = [], []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: info.append(args[-1]))
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[-1]))
    return info, errors


@pytest.mark.parametrize('mode', ['full', 'words', 'cancel'])
def test_ui_import_modes(source, window, monkeypatch, tmp_path, mode):
    add(window.repository, 'local')
    page = enter(window, monkeypatch, AM)
    before = window.repository.study_backup()
    info, errors = import_ui(window, monkeypatch, tmp_path, source.study_backup(), mode)
    window.import_words()
    assert not errors
    if mode == 'full':
        assert window.repository.study_backup().records == source.study_backup().records
        assert page.tasks == []
        assert len(window.repository.list_entries()) == 2
    elif mode == 'words':
        assert window.repository.study_backup() == before
        assert len(window.repository.list_entries()) == 2
    else:
        assert window.repository.study_backup() == before
        assert len(window.repository.list_entries()) == 1 and not info


@pytest.mark.parametrize('incoming', [True, False, None])
def test_study_import_reuses_duplicate_dialog_and_final_local_id(source, window, monkeypatch, tmp_path, incoming):
    old = add(window.repository, 'ALPHA', '本机内容')
    enter(window, monkeypatch)
    info, errors = import_ui(window, monkeypatch, tmp_path, source.study_backup())
    seen = []
    def resolve(dialog):
        seen.append(dialog)
        if incoming is None:
            dialog.reject()
        else:
            dialog.choose_all(incoming)
        return 0
    monkeypatch.setattr(ImportConflictDialog, 'exec', resolve)
    window.import_words()
    assert len(seen) == 1 and info and not errors
    restored = window.repository.study_backup()
    assert restored.records == source.study_backup().records
    assert (restored.words[0].id != old.id) == (incoming is True)
    assert restored.words[0].senses[0].translation == ('中文\n第二行' if incoming else '本机内容')


def test_words_only_overwrite_keeps_local_progress_after_id_changes(source, window, monkeypatch, tmp_path):
    old = add(window.repository, 'Alpha', '本机')
    enter(window, monkeypatch, AM)
    before = window.repository.study_backup().records
    info, errors = import_ui(window, monkeypatch, tmp_path, source.study_backup(), 'words')
    monkeypatch.setattr(ImportConflictDialog, 'exec', lambda dialog: dialog.choose_all(True) or 0)
    window.import_words()
    assert not errors and info
    assert window.repository.study_backup().records == before
    assert window.repository.study_backup().words[0].id != old.id


def test_import_reloads_clock_but_does_not_draw_until_explicit_refresh(source, window, monkeypatch, tmp_path):
    local = add(window.repository, 'local')
    enter(window, monkeypatch)
    info, errors = import_ui(window, monkeypatch, tmp_path, source.study_backup())
    tomorrow = StudyContext(DAY + timedelta(days=1), False)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: tomorrow))
    window.import_words()
    assert info and not errors
    assert window.study_page.context == tomorrow
    assert len(window.repository.study_backup().records) == 1
    window.study_page.reload()
    assert len(window.repository.study_backup().records) == 2
    assert local.id in [task.entry.id for task in window.study_page.tasks]


def test_ui_progress_failure_reports_saved_words_and_keeps_prior_progress(source, window, monkeypatch, tmp_path):
    add(window.repository, 'local')
    enter(window, monkeypatch, AM)
    before = window.repository.study_backup()
    info, errors = import_ui(window, monkeypatch, tmp_path, source.study_backup())
    window.repository.connection.execute('''CREATE TRIGGER fail_restore BEFORE INSERT ON memorize_vocabulary_17day
        BEGIN SELECT RAISE(ABORT,'restore failed'); END''')
    window.import_words()
    assert not info and len(errors) == 1 and '词条已保留' in errors[0]
    assert window.repository.study_backup() == before
    assert len(window.repository.list_entries()) == 2


def test_invalid_file_and_cancellation_never_save_hidden_draft(source, window, monkeypatch, tmp_path):
    add(window.repository, 'local')
    window.blocks[0].word.setText('草稿')
    enter(window, monkeypatch, AM)
    before = window.repository.study_backup()
    info, errors = import_ui(window, monkeypatch, tmp_path, source.study_backup(), 'cancel')
    original_resolve = window.resolve_dirty
    monkeypatch.setattr(window, 'resolve_dirty', lambda: pytest.fail('should not touch the draft'))
    window.import_words()
    assert not errors and not info and window.blocks[0].word.text() == '草稿'
    (tmp_path / 'study.json').write_text('{}', encoding='utf-8')
    window.import_words()
    assert len(errors) == 1 and window.repository.study_backup() == before
    monkeypatch.setattr(window, 'resolve_dirty', original_resolve)


@pytest.mark.parametrize('choice,expected', [('导入单词和进度', 'full'), ('仅导入单词', 'words'), ('取消', 'cancel')])
def test_actual_three_button_choice(window, choice, expected):
    def select():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert '覆盖本机全部学习进度' in dialog.text()
        next(button for button in dialog.buttons() if button.text() == choice).click()
    QTimer.singleShot(0, select)
    assert window.choose_study_import_mode() == expected


@pytest.mark.parametrize('collapsed', [False, True])
def test_hidden_form_preserves_draft_and_blocks_all_form_shortcuts(window, qtbot, monkeypatch, collapsed):
    window.blocks[0].word.setText('draft')
    window.set_form_collapsed(collapsed)
    enter(window, monkeypatch)
    assert not window.editor_panel.isVisible()
    value = window.form_value()
    for callback in window.shortcuts.callbacks.values():
        callback()
    qtbot.keyClick(window.study_page.table, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(window.study_page.table, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert window.form_value() == value and window.form_collapsed == collapsed
    assert window.repository.list_entries() == []
    assert not window.editor_panel.isVisible()
    window.set_filter('pending')
    assert window.editor_panel.isVisible() and window.form_collapsed == collapsed
    assert window.form_value() == value
    window.blocks[0].word.clear()


def test_study_export_button_routes_independently_and_uses_visible_error_anchor(source, window, monkeypatch, tmp_path):
    add(window.repository)
    enter(window, monkeypatch, AM)
    path = tmp_path / 'progress'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    window.export_words()
    assert read_study_backup(path.with_suffix('.json')).records == window.repository.study_backup().records
    anchors = []
    monkeypatch.setattr(window.toast, 'show_message', lambda text, anchor, **kwargs: anchors.append(anchor))
    window.show_notice('test failure', True)
    assert anchors == [window.study_page.refresh_button] and anchors[0].isVisible()

"""Status badges save independently; choosing a card still takes one click."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from vocabulary.domain import Sense, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_word_or_study_file, read_study_backup, serialize_study_backup
from vocabulary.transfer import ImportSource, read_entries, serialize_entries


def comparison(repo, qtbot, tmp_path):
    original = repo.save(WordEntry('same', [Sense('n.', '本地')], important=False))
    # Sorting changes the display order; both occurrences must retain their source indices.
    candidates = [WordEntry('SAME', [Sense('v.', '第一个')], important=False, sort_order=3),
                  WordEntry('other', [Sense('n.', '其他')], sort_order=2),
                  WordEntry('Same', [Sense('adj.', '第二个')], important=False, sort_order=1)]
    path = tmp_path / 'import.json'
    content = serialize_entries(candidates)
    path.write_text(content, encoding='utf-8-sig')
    source, entries = ImportSource.read(path, parse_word_or_study_file)
    dialog = ImportConflictDialog(repo, repo.import_entries(entries), source=source)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog, original, path, json.loads(content)


@pytest.mark.parametrize('flag,control_name', [('important', 'star'), ('learned', 'learned')])
def test_left_badge_saves_database_syncs_same_word_and_does_not_choose(
        repo, qtbot, tmp_path, flag, control_name):
    dialog, original, path, before = comparison(repo, qtbot, tmp_path)
    source_bytes = path.read_bytes()
    repo.save(WordEntry('already learned', [Sense('n.', '已记')], learned=True))
    left, right = dialog.cards[0]
    qtbot.mouseClick(getattr(left, control_name), Qt.MouseButton.RightButton)
    saved = repo.get(original.id)
    assert getattr(saved, flag) is True
    assert all(getattr(pair[0].entry, flag) is True for pair in dialog.cards)
    assert saved.id == original.id and saved.senses == original.senses
    if flag == 'learned':
        assert saved.sort_order > original.sort_order
    assert path.read_bytes() == source_bytes
    assert getattr(right.entry, flag) is False
    assert len(dialog.conflicts) == 2 and dialog.isVisible() and not dialog.replaced_names
    # Clicking any ordinary content area still adopts in a single click.
    qtbot.mouseClick(left.word, Qt.MouseButton.LeftButton)
    assert len(dialog.conflicts) == 1 and getattr(repo.get(original.id), flag) is True
    dialog.reject()
    assert getattr(repo.get(original.id), flag) is True


@pytest.mark.parametrize('flag,control_name', [('important', 'star'), ('learned', 'learned')])
def test_right_badge_writes_exact_record_then_single_click_adopts_modified_value(
        repo, qtbot, tmp_path, flag, control_name):
    dialog, original, path, before = comparison(repo, qtbot, tmp_path)
    assert dialog.conflicts[0].source_index == 2
    right = dialog.cards[0][1]
    qtbot.mouseClick(getattr(right, control_name), Qt.MouseButton.RightButton)
    expected = deepcopy(before)
    expected['words'][2][flag] = True
    assert json.loads(path.read_text(encoding='utf-8')) == expected
    assert getattr(dialog.conflicts[0].incoming, flag) is True
    assert getattr(dialog.conflicts[1].incoming, flag) is False
    assert repo.get(original.id) == original
    assert len(dialog.conflicts) == 2 and dialog.isVisible()
    qtbot.mouseClick(right.word, Qt.MouseButton.LeftButton)
    adopted = next(entry for entry in repo.list_entries() if entry.text == 'Same')
    assert getattr(adopted, flag) is True and adopted.id == original.id
    assert len(dialog.conflicts) == 1
    # The updated local card remains editable after adoption changed the local ID.
    qtbot.mouseClick(getattr(dialog.cards[0][0], control_name), Qt.MouseButton.RightButton)
    assert getattr(repo.get(adopted.id), flag) is False
    assert json.loads(path.read_text(encoding='utf-8')) == expected
    dialog.reject()
    assert getattr(read_entries(path)[2], flag) is True


def test_badges_ignore_keyboard_without_choosing(repo, qtbot, tmp_path):
    dialog, original, path, before = comparison(repo, qtbot, tmp_path)
    before_file = path.read_bytes()
    for left, right in dialog.cards[:1]:
        for control in (left.star, left.learned, right.star, right.learned):
            control.setFocus()
            qtbot.keyClick(control, Qt.Key.Key_Space)
            qtbot.keyClick(control, Qt.Key.Key_Space)
    assert len(dialog.conflicts) == 2 and dialog.isVisible()
    assert not repo.get(original.id).important and not repo.get(original.id).learned
    assert path.read_bytes() == before_file
    dialog.reject()


def test_bulk_choice_uses_edited_candidates_in_existing_order(repo, qtbot, tmp_path):
    dialog, original, path, before = comparison(repo, qtbot, tmp_path)
    # The last displayed candidate wins for a repeated word in a bulk adoption.
    right = dialog.cards[-1][1]
    qtbot.mouseClick(right.star, Qt.MouseButton.RightButton)
    qtbot.mouseClick(right.learned, Qt.MouseButton.RightButton)
    qtbot.mouseClick(dialog.all_right_button, Qt.MouseButton.LeftButton)
    adopted = next(entry for entry in repo.list_entries() if entry.text == 'SAME')
    assert adopted.important and adopted.learned and adopted.id == original.id
    assert not dialog.conflicts and dialog.replaced_names == {'same'}
    expected = deepcopy(before)
    expected['words'][0].update(important=True, learned=True)
    assert json.loads(path.read_text(encoding='utf-8')) == expected


@pytest.mark.parametrize('failure', ['write', 'external_change', 'database'])
def test_save_failure_preserves_card_state_and_allows_retry(repo, qtbot, tmp_path, monkeypatch, failure):
    dialog, original, path, before = comparison(repo, qtbot, tmp_path)
    messages = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: messages.append(args[-1]))
    left, right = dialog.cards[0]
    if failure == 'write':
        def fail(*args):
            raise OSError('file locked')
        with monkeypatch.context() as patch:
            patch.setattr('vocabulary.transfer.os.replace', fail)
            qtbot.mouseClick(right.star, Qt.MouseButton.RightButton)
        assert json.loads(path.read_text(encoding='utf-8-sig')) == before
        assert not list(tmp_path.glob('import.json.*.tmp'))
    elif failure == 'external_change':
        edited = path.read_text(encoding='utf-8-sig') + '\n'
        path.write_text(edited, encoding='utf-8')
        qtbot.mouseClick(right.star, Qt.MouseButton.RightButton)
        assert path.read_text(encoding='utf-8') == edited
        path.write_text(dialog.source.content, encoding='utf-8')
    else:
        def fail(*args):
            raise OSError('database locked')
        with monkeypatch.context() as patch:
            patch.setattr(Repository, 'set_flag', fail)
            qtbot.mouseClick(left.star, Qt.MouseButton.RightButton)
    assert messages and not left.entry.important and not right.entry.important
    assert repo.get(original.id) == original
    assert len(dialog.conflicts) == 2 and not dialog.busy_overlay.active
    qtbot.mouseClick(left.star if failure == 'database' else right.star, Qt.MouseButton.RightButton)
    assert (left if failure == 'database' else right).entry.important
    assert len(dialog.conflicts) == 2
    dialog.reject()


@pytest.mark.parametrize('study,page,mode', [(False, 'pending', 'words'),
                                          (True, 'pending', 'full'),
                                          (True, 'study17', 'words')])
def test_window_passes_source_for_each_import_route(
        window, repo, qtbot, tmp_path, monkeypatch, study, page, mode):
    original = repo.save(WordEntry('same', [Sense('n.', '本地')], important=False))
    candidate = replace(original, id=None, senses=[Sense('v.', '导入')])
    if study:
        repo.prepare_study(StudyContext(date(2026, 11, 22), False))
        backup = repo.study_backup()
        backup.words = [candidate]
        content = serialize_study_backup(backup)
    else:
        content = serialize_entries([candidate])
    document = json.loads(content)
    document['words'][0].pop('sort_order', None)
    document['words'][0]['phonetic'] = '  /sound/  '
    content = json.dumps(document, ensure_ascii=False)
    path = tmp_path / 'source.json'
    path.write_text(content, encoding='utf-8')
    before = json.loads(content)
    window.set_filter(page)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(window, 'choose_study_import_mode', lambda: mode)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: pytest.fail(args[-1]))
    seen = []

    def review(dialog):
        seen.append(dialog.source.path)
        qtbot.mouseClick(dialog.cards[0][1].star, Qt.MouseButton.RightButton)
        qtbot.mouseClick(dialog.cards[0][1].learned, Qt.MouseButton.RightButton)
        assert len(dialog.conflicts) == 1
        # Closing keeps edits in the source without adopting the candidate.
        dialog.reject()
        return 0

    monkeypatch.setattr(ImportConflictDialog, 'exec', review)
    window.import_words()
    expected = deepcopy(before)
    expected['words'][0].update(important=True, learned=True)
    assert seen == [path]
    assert json.loads(path.read_text(encoding='utf-8')) == expected
    assert repo.get(original.id) == original
    if study:
        assert read_study_backup(path).records == backup.records

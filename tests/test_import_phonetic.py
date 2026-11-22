"""Incoming pronunciations sync independently from adopting a conflict card."""
from dataclasses import replace
from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_word_or_study_file
from vocabulary.transfer import ImportSource, read_entries, write_entries


@pytest.mark.parametrize('raw,expected', [
    (' /ˈrekərd/ ', 'ˈrekərd'), ('///sound///', 'sound'),
    ('/ / sound / /', 'sound'), ('/sound', 'sound'), ('sound/', 'sound'),
    (' /a/b/ ', 'a/b'), ('[sound]', '[sound]'), ('plain', 'plain'),
    (' / / ', ''), ('', ''),
])
def test_import_cleans_main_and_related_sounds_before_matching_and_preserves_json(
        repo, tmp_path, raw, expected):
    local = WordEntry('same', [Sense('n.', '释义')],
                      [RelatedWord('child', [Sense('n.', '关联')], expected)], phonetic=expected)
    original = repo.save(local)
    incoming = replace(local, phonetic=raw,
                       related=[replace(local.related[0], phonetic=raw)])
    path = tmp_path / 'raw.json'
    write_entries(path, [incoming, replace(incoming, text='new')])
    before_file = path.read_bytes()
    drafts = read_entries(path)
    result = repo.import_entries(drafts)
    assert result.inserted == 1 and not result.conflicts
    assert repo.get(original.id) == original
    added = next(entry for entry in repo.list_entries() if entry.text == 'new')
    assert added.phonetic == added.related[0].phonetic == expected
    assert path.read_bytes() == before_file
    assert drafts[0].phonetic == drafts[0].related[0].phonetic == raw


def test_cleaned_empty_sounds_have_no_clickable_phonetic_labels(repo, qtbot):
    original = repo.save(WordEntry('same', [Sense('n.', '本地')]))
    candidate = replace(original, phonetic='///', senses=[Sense('v.', '导入')],
                        related=[RelatedWord('child', [Sense('n.', '关联')], '/ /')])
    dialog = ImportConflictDialog(repo, repo.import_entries([candidate]))
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.cards[0][1].description.phonetic_labels
    assert repo.get(original.id) == original
    dialog.reject()


def comparison(repo, qtbot, tmp_path, phonetic='/old/'):
    original = repo.save(WordEntry('record', [Sense('n.', '本地释义'), Sense('v.', '本地动词')],
        [RelatedWord('other', [Sense('n.', '其他')], '/other/'),
         RelatedWord('recorder', [Sense('n.', '录音机')], '/local-child/')],
        important=False, phonetic=phonetic))
    repo.prepare_study(StudyContext(date(2026, 11, 22), False))
    candidate = WordEntry('RECORD', [Sense('adj.', '不同释义')],
        [RelatedWord('RECORDER', [Sense('v.', '不同关联释义')], '/incoming-child/')],
        important=True, learned=True, phonetic='/' + 'long sound ' * 30 + '/')
    path = tmp_path / 'phonetics.json'
    write_entries(path, [candidate, replace(candidate, text='Record')])
    source, entries = ImportSource.read(path, parse_word_or_study_file)
    dialog = ImportConflictDialog(repo, repo.import_entries(entries), source=source)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(10)
    return dialog, original, path


@pytest.mark.parametrize('old_phonetic', ['', '/old/'])
def test_main_phonetic_sync_saves_only_full_pronunciation_and_keeps_cards(
        repo, qtbot, tmp_path, old_phonetic):
    dialog, original, path = comparison(repo, qtbot, tmp_path, old_phonetic)
    before_file = path.read_bytes()
    before_study = repo.study_backup()
    before_senses = list(repo.connection.execute('SELECT * FROM senses ORDER BY id'))
    before_related = list(repo.connection.execute('SELECT * FROM related_words ORDER BY id'))
    right = dialog.cards[0][1]
    sound = right.description.phonetic_labels[-1]
    assert sound.display_text.endswith('...')
    qtbot.mouseClick(sound, Qt.MouseButton.RightButton)
    expected = replace(original, phonetic=right.entry.phonetic)
    assert repo.get(original.id) == expected
    assert all(left.entry == expected for left, _ in dialog.cards)
    assert all(left.description.phonetic_labels[-1].text() == right.entry.phonetic.strip('/').strip()
               for left, _ in dialog.cards)
    assert len(dialog.conflicts) == 2 and dialog.isVisible() and not dialog.replaced_names
    assert path.read_bytes() == before_file
    assert repo.study_backup() == replace(before_study, words=[expected])
    assert list(repo.connection.execute('SELECT * FROM senses ORDER BY id')) == before_senses
    assert list(repo.connection.execute('SELECT * FROM related_words ORDER BY id')) == before_related
    assert dialog.summary.accessibleName().endswith('鼠标右键 相应位置修改或同步单词信息')
    # Rebuilding the local description retains the independent badge interactions.
    qtbot.mouseClick(dialog.cards[0][0].star, Qt.MouseButton.RightButton)
    assert repo.get(original.id).important and len(dialog.conflicts) == 2
    qtbot.mouseClick(dialog.cards[0][0].word, Qt.MouseButton.LeftButton)
    assert len(dialog.conflicts) == 1 and repo.get(original.id).phonetic == expected.phonetic
    dialog.reject()
    reopened = Repository(repo.path)
    try:
        assert reopened.get(original.id).phonetic == expected.phonetic
    finally:
        reopened.close()


def test_related_sound_matches_name_instead_of_position_and_preserves_local_content(repo, qtbot, tmp_path):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    before_file = path.read_bytes()
    right = dialog.cards[0][1]
    qtbot.mouseClick(right.description.phonetic_labels[0], Qt.MouseButton.RightButton)
    expected = replace(original, related=[original.related[0],
        replace(original.related[1], phonetic='incoming-child')])
    assert repo.get(original.id) == expected
    assert all(left.entry == expected for left, _ in dialog.cards)
    assert len(dialog.conflicts) == 2 and not dialog.replaced_names
    assert path.read_bytes() == before_file
    dialog.reject()


@pytest.mark.parametrize('problem', ['missing', 'ambiguous'])
def test_missing_related_adds_word_and_duplicate_related_sound_updates_first(repo, qtbot, problem):
    related = [] if problem == 'missing' else [
        RelatedWord('child', [Sense('n.', '一')]), RelatedWord('CHILD', [Sense('n.', '二')])]
    original = repo.save(WordEntry('main', [Sense('n.', '本地')], related))
    candidate = WordEntry('main', [Sense('v.', '导入')],
                          [RelatedWord('child', [Sense('n.', '关联词')], '/sound/')])
    dialog = ImportConflictDialog(repo, repo.import_entries([candidate]))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.cards[0][1].description.phonetic_labels[0], Qt.MouseButton.RightButton)
    expected_related = ([replace(candidate.related[0], phonetic='sound')] if problem == 'missing' else
                        [replace(original.related[0], phonetic='sound'), original.related[1]])
    assert repo.get(original.id) == replace(original, related=expected_related)
    assert len(dialog.conflicts) == 1 and not dialog.replaced_names and dialog.isVisible()
    dialog.reject()


def test_database_failure_keeps_old_sound_and_same_label_can_retry(repo, qtbot, tmp_path, monkeypatch):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    before_file = path.read_bytes()
    repo.connection.execute('''CREATE TRIGGER fail_sound BEFORE UPDATE OF phonetic ON words
        BEGIN SELECT RAISE(ABORT, 'sound locked'); END''')
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[-1]))
    right = dialog.cards[0][1]
    sound = right.description.phonetic_labels[-1]
    qtbot.mouseClick(sound, Qt.MouseButton.RightButton)
    assert errors and 'sound locked' in errors[0]
    assert repo.get(original.id) == original and dialog.cards[0][0].entry == original
    assert not dialog.busy_overlay.active and len(dialog.conflicts) == 2
    repo.connection.execute('DROP TRIGGER fail_sound')
    qtbot.mouseClick(sound, Qt.MouseButton.RightButton)
    assert repo.get(original.id).phonetic == right.entry.phonetic
    assert len(dialog.conflicts) == 2 and path.read_bytes() == before_file
    dialog.reject()


def test_sync_equalizes_content_but_normal_click_still_adopts_once(repo, qtbot):
    original = repo.save(WordEntry('same', [Sense('n.', '释义')]))
    candidate = replace(original, phonetic='/new/')
    dialog = ImportConflictDialog(repo, repo.import_entries([candidate]))
    qtbot.addWidget(dialog)
    dialog.show()
    right = dialog.cards[0][1]
    qtbot.mouseClick(right.description.phonetic_labels[-1], Qt.MouseButton.RightButton)
    assert len(dialog.conflicts) == 1 and dialog.isVisible()
    assert repo.get(original.id) == replace(original, phonetic='new')
    qtbot.mouseClick(right.word, Qt.MouseButton.LeftButton)
    assert not dialog.conflicts and dialog.replaced_names == {'same'}
    assert repo.list_entries()[0].id == original.id


def test_local_phonetic_retains_single_click_choice(repo, qtbot):
    original = repo.save(WordEntry('same', [Sense('n.', '本地')], phonetic='/local/'))
    candidate = replace(original, phonetic='/incoming/')
    dialog = ImportConflictDialog(repo, repo.import_entries([candidate]))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.cards[0][0].description.phonetic_labels[-1], Qt.MouseButton.LeftButton)
    assert not dialog.conflicts and not dialog.replaced_names
    assert repo.get(original.id) == original


def test_window_defers_refresh_until_comparison_closes(window, repo, qtbot, tmp_path, monkeypatch):
    original = repo.save(WordEntry('same', [Sense('n.', '本地')]))
    window.refresh()
    path = tmp_path / 'sync.json'
    write_entries(path, [replace(original, phonetic='/incoming/')])
    before_file = path.read_bytes()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: pytest.fail(args[-1]))
    seen = []
    refreshes = []
    refresh = window.refresh_after_transfer

    def record_refresh(*args):
        refreshes.append(True)
        return refresh(*args)

    monkeypatch.setattr(window, 'refresh_after_transfer', record_refresh)

    def review(dialog):
        before = len(refreshes)
        qtbot.mouseClick(dialog.cards[0][1].description.phonetic_labels[-1], Qt.MouseButton.RightButton)
        assert repo.get(original.id).phonetic == 'incoming'
        assert len(refreshes) == before
        assert window.model.rows[0].entry.phonetic == original.phonetic
        assert len(dialog.conflicts) == 1
        seen.append(True)
        dialog.reject()
        return 0

    monkeypatch.setattr(ImportConflictDialog, 'exec', review)
    window.import_words()
    assert seen and repo.get(original.id) == replace(original, phonetic='incoming')
    assert len(refreshes) == 1
    assert window.model.rows[0].entry.phonetic == 'incoming'
    assert path.read_bytes() == before_file

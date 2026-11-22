"""Comparison edits and choices save immediately without refreshing the main page."""
from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ConflictCard, ImportConflictDialog
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import write_study_backup
from vocabulary.transfer import read_entries, write_entries


@pytest.mark.parametrize('route,finish', [
    ('words', 'close'), ('words', 'escape'), ('words', 'adopt_one'),
    ('study_words', 'close'), ('study_full', 'adopt_all'),
])
def test_edits_and_choices_defer_main_refresh_and_full_reads_until_exit(
        window, repo, qtbot, tmp_path, monkeypatch, route, finish):
    originals = [repo.save(WordEntry(name, [Sense('n.', '本地')], important=False))
                 for name in ('same', 'second')]
    candidates = [WordEntry(name, [Sense('v.', '导入')],
                           [RelatedWord('new-child', [Sense('n.', '关联')], 'child')],
                           important=False, phonetic='/sound/')
                  for name in ('same', 'second', 'new')]
    path = tmp_path / 'import.json'
    if route == 'words':
        write_entries(path, candidates)
    else:
        source = Repository(tmp_path / 'source.sqlite3')
        try:
            for entry in candidates:
                source.save(entry)
            source.prepare_study(StudyContext(date(2026, 11, 22), False))
            write_study_backup(path, source.study_backup())
        finally:
            source.close()
        monkeypatch.setattr(window, 'choose_study_import_mode',
                            lambda: 'full' if route == 'study_full' else 'words')
        if route == 'study_full':
            window.set_filter('study17')
    window.refresh()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: pytest.fail(args[-1]))
    refreshes, reads, seen = [], [], []
    refresh = window.refresh
    list_entries = Repository.list_entries

    def record_refresh(*args, **kwargs):
        refreshes.append(True)
        return refresh(*args, **kwargs)

    def record_read(repository):
        reads.append(True)
        return list_entries(repository)

    monkeypatch.setattr(window, 'refresh', record_refresh)
    monkeypatch.setattr(Repository, 'list_entries', record_read)
    card_updates = []
    set_entry, update_context = ConflictCard.set_entry, ConflictCard.update_context

    def record_entry(card, entry):
        card_updates.append(('content', card.side, entry.text.casefold()))
        return set_entry(card, entry)

    def record_context(card, entry):
        if card.side == 'right':
            card_updates.append(('context', card.side, entry.text.casefold()))
        return update_context(card, entry)

    monkeypatch.setattr(ConflictCard, 'set_entry', record_entry)
    monkeypatch.setattr(ConflictCard, 'update_context', record_context)

    def review(dialog):
        dialog.show()
        assert not refreshes and not reads
        left, right = dialog.cards[0]
        for control in (left.star, left.learned, right.star, right.learned,
                        right.description.phonetic_labels[-1], right.description.sense_labels[-1, 0][0],
                        right.description.word_labels[1]):
            card_updates.clear()
            qtbot.mouseClick(control, Qt.MouseButton.RightButton)
            assert not refreshes and not reads
            assert len(dialog.conflicts) == 2
            assert all(name == 'same' for _, _, name in card_updates)
            if control in (right.description.phonetic_labels[-1], right.description.sense_labels[-1, 0][0],
                           right.description.word_labels[1]):
                assert card_updates == [('content', 'left', 'same'), ('context', 'right', 'same')]
        current = repo.get(originals[0].id)
        assert current.important and current.learned and current.phonetic == 'sound'
        assert left.entry == current
        if route == 'words':
            saved = read_entries(path)[0]
            assert saved.important and saved.learned and saved.phonetic == '/sound/'
        # Both retaining a local card and adopting an incoming card stay within the dialog.
        qtbot.mouseClick(dialog.cards[1][0].word, Qt.MouseButton.LeftButton)
        assert not refreshes and not reads and len(dialog.conflicts) == 1
        if finish in ('adopt_one', 'adopt_all'):
            qtbot.mouseClick(right.word if finish == 'adopt_one' else dialog.all_right_button,
                             Qt.MouseButton.LeftButton)
        else:
            # Keep one pending pair so close/Esc also cover partially resolved imports.
            if finish == 'close':
                dialog.close()
            else:
                qtbot.keyClick(dialog, Qt.Key.Key_Escape)
        assert not refreshes and not reads and not dialog.isVisible()
        seen.append(dialog.replaced_ids)
        return dialog.result()

    monkeypatch.setattr(ImportConflictDialog, 'exec', review)
    window.import_words()
    assert len(seen) == 1 and len(refreshes) == 1
    final = repo.get(originals[0].id) if finish not in ('adopt_one', 'adopt_all') else next(
        entry for entry in list_entries(repo) if entry.text == 'same')
    assert final.important and final.learned and final.phonetic == 'sound'
    assert seen[0] == ({final.id} if finish in ('adopt_one', 'adopt_all') else set())
    assert repo.get(originals[1].id) == originals[1]

"""Manual adoption keeps local identity and moves only on a real state change."""
from dataclasses import replace
from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository


@pytest.mark.parametrize('bulk', [False, True], ids=['single', 'all'])
@pytest.mark.parametrize('local_learned,incoming_learned', [
    (False, False), (True, True), (False, True), (True, False),
], ids=['pending-stays', 'learned-stays', 'to-learned', 'to-pending'])
def test_manual_adoption_preserves_identity_creation_and_study_state(
        repo, qtbot, local_learned, incoming_learned, bulk):
    main = repo.save(WordEntry('record', [Sense('n.', '本地')], related=[
        RelatedWord('local-child', [Sense('adj.', '本地关联词')])]))
    context = StudyContext(date(2026, 11, 22), False)
    repo.prepare_study(context)
    repo.complete_study(main.id, context)
    repo.set_flag(main.id, 'learned', local_learned)
    main = repo.get(main.id)
    originals = [main]
    if bulk:
        originals.append(repo.save(WordEntry('second', [Sense('n.', '第二个')], learned=local_learned)))
    for state in (False, True):
        repo.save(WordEntry(f'tail-{state}', [Sense('n.', '末尾')], learned=state))
    with repo.connection:
        repo.connection.execute("UPDATE words SET created_at='2001-02-03T04:05:06.000Z'")
    created = dict(repo.connection.execute('SELECT id,created_at FROM words'))
    before_order = repo.list_entries()
    counters = [repo.max_pending_sort_order, repo.max_learned_sort_order]
    progress = [tuple(row) for row in repo.connection.execute('SELECT * FROM memorize_vocabulary_17day')]
    history = repo.study_task_histories()
    assert progress and history
    # Reusing an ID after a delete or rebuilding progress would also violate these guards.
    repo.connection.execute("""CREATE TRIGGER keep_main BEFORE DELETE ON words
        BEGIN SELECT RAISE(ABORT, 'main must not be deleted'); END""")
    for operation in ('INSERT', 'DELETE', 'UPDATE'):
        repo.connection.execute(f'''CREATE TRIGGER keep_progress_{operation}
            BEFORE {operation} ON memorize_vocabulary_17day
            BEGIN SELECT RAISE(ABORT, 'progress must not be rewritten'); END''')
    candidates = [WordEntry(entry.text.upper(), [Sense('v.', '导入'), Sense('n.', '导入名词')],
                            related=[RelatedWord('new-child', [Sense('adv.', '导入关联词')], 'child-sound')],
                            phonetic='incoming', learned=incoming_learned, important=False,
                            id=999, sort_order=999999)
                  for entry in originals]
    result = repo.import_entries(candidates)
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.all_right_button if bulk else dialog.cards[0][1].word,
                     Qt.MouseButton.LeftButton)

    assert not dialog.conflicts and not dialog.isVisible()
    assert dialog.replaced_ids == {entry.id for entry in originals}
    for original, incoming in zip(originals, candidates):
        saved = repo.get(original.id)
        assert saved == replace(incoming, id=original.id, sort_order=saved.sort_order)
        if local_learned == incoming_learned:
            assert saved.sort_order == original.sort_order
    assert dict(repo.connection.execute('SELECT id,created_at FROM words')) == created
    assert progress == [tuple(row) for row in repo.connection.execute('SELECT * FROM memorize_vocabulary_17day')]
    assert repo.study_task_histories() == history
    if local_learned == incoming_learned:
        assert [entry.id for entry in repo.list_entries()] == [entry.id for entry in before_order]
    else:
        existing_target = [entry.id for entry in before_order if entry.learned == incoming_learned]
        assert [entry.id for entry in repo.list_entries() if entry.learned == incoming_learned] == (
            existing_target + [entry.id for entry in originals])
        counters[int(incoming_learned)] += len(originals)
    assert counters == [repo.max_pending_sort_order, repo.max_learned_sort_order]
    reopened = Repository(repo.path)
    try:
        assert reopened.list_entries() == repo.list_entries()
        assert dict(reopened.connection.execute('SELECT id,created_at FROM words')) == created
        assert reopened.study_task_histories() == history
    finally:
        reopened.close()


def test_same_name_batch_uses_latest_state_without_allocating_order_for_unchanged_state(repo):
    original = repo.save(WordEntry('same', [Sense('n.', '本地')]))
    repo.save(WordEntry('pending-tail', [Sense('n.', '待记末尾')]))
    repo.save(WordEntry('learned-tail', [Sense('n.', '已记末尾')], learned=True))
    candidates = [WordEntry('SAME', [Sense('n.', text)], learned=state)
                  for state, text in ((True, '第一次'), (True, '第二次'), (False, '第三次'))]
    choices = repo.import_entries(candidates).conflicts
    adopted = repo.replace_imports(choices)['same']
    assert adopted.id == original.id and adopted.senses == [Sense('n.', '第三次')]
    assert not adopted.learned and adopted.sort_order == 3
    # The second candidate did not advance the learned category's high-water mark.
    assert repo.max_learned_sort_order == 2 and repo.max_pending_sort_order == 3
    assert all(choice.current == original for choice in choices)


def test_state_update_failure_rolls_back_prior_content_state_order_and_metadata(repo):
    originals = [repo.save(WordEntry(name, [Sense('n.', '本地')])) for name in ('one', 'two')]
    choices = repo.import_entries([
        WordEntry(entry.text.upper(), [Sense('v.', '导入')], learned=True, important=False)
        for entry in originals]).conflicts
    before = [tuple(row) for row in repo.connection.execute('SELECT * FROM words ORDER BY id')]
    repo.connection.execute("""CREATE TRIGGER fail_state BEFORE UPDATE OF learned ON words
        WHEN NEW.text='TWO' BEGIN SELECT RAISE(ABORT, 'state locked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='state locked'):
        repo.replace_imports(choices)
    assert repo.list_entries() == originals
    assert [tuple(row) for row in repo.connection.execute('SELECT * FROM words ORDER BY id')] == before
    assert (repo.max_pending_sort_order, repo.max_learned_sort_order) == (2, 0)
    repo.connection.execute('DROP TRIGGER fail_state')
    assert {entry.id for entry in repo.replace_imports(choices).values()} == {entry.id for entry in originals}

import sqlite3

import pytest

from vocabulary.domain import DuplicateWord, RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.storage import Repository


def example():
    return WordEntry("record", [Sense("n.", "记录；唱片\n第二行"), Sense("v.", "记录；录制")],
                     [RelatedWord("recorder", [Sense("n.", "录音机；记录员")]),
                      RelatedWord("recording", [Sense("n.", "录音"), Sense("adj.", "记录用的")])])


def test_aggregate_roundtrip_and_restart(repo):
    saved = repo.save(example())
    assert saved.senses[0].translation.endswith("第二行")
    assert len(saved.related) == 2
    assert saved.related[1].senses[1].pos == "adj."
    repo.set_flag(saved.id, "learned", True)
    repo.set_flag(saved.id, "important", False)
    second = Repository(repo.path)
    try:
        restored = second.get(saved.id)
        assert restored.learned and not restored.important
        assert restored.related == saved.related
    finally:
        second.close()


def test_casefold_duplicate_preserves_existing(repo):
    saved = repo.save(example())
    with pytest.raises(DuplicateWord) as info:
        repo.save(WordEntry("  RECORD  ", [Sense("v.", "different")]))
    assert info.value.entry_id == saved.id
    assert len(repo.list_entries()) == 1
    assert repo.get(saved.id).senses == saved.senses


def test_update_keeps_id_and_current_learning_state(repo):
    saved = repo.save(example())
    draft = repo.get(saved.id)
    repo.set_flag(saved.id, "learned", True)
    draft.senses[0].translation = "新内容"
    updated = repo.save(draft)
    assert updated.id == saved.id and updated.learned
    assert updated.senses[0].translation == "新内容"
    assert len(updated.related) == 2


def test_delete_cascades(repo):
    saved = repo.save(example())
    repo.delete(saved.id)
    for table in ("words", "related_words", "senses"):
        assert repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_failed_transaction_leaves_original_aggregate(repo):
    saved = repo.save(example())
    repo.connection.execute("""CREATE TRIGGER fail_sense BEFORE INSERT ON senses
        WHEN NEW.translation='FAIL' BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
    draft = repo.get(saved.id)
    draft.senses[0].translation = "FAIL"
    with pytest.raises(sqlite3.IntegrityError):
        repo.save(draft)
    assert repo.get(saved.id) == saved


def test_blank_extra_rows_are_ignored_but_partial_rows_rejected(repo):
    entry = example()
    entry.senses.append(Sense())
    entry.related.append(RelatedWord())
    saved = repo.save(entry)
    assert len(saved.senses) == 2 and len(saved.related) == 2
    entry.senses.append(Sense("n.", ""))
    with pytest.raises(ValidationError):
        repo.save(entry)


def test_related_names_are_not_globally_unique(repo):
    entry = example()
    entry.related.append(RelatedWord("record", [Sense("v.", "同名允许")]))
    repo.save(entry)
    second = example()
    second.text = "records"
    repo.save(second)
    assert len(repo.list_entries()) == 2


def test_shortcuts_restart_does_not_overwrite_user_settings(repo):
    repo.write_shortcuts({"add_related": "Alt+N"})
    second = Repository(repo.path)
    try:
        assert second.shortcuts()["add_related"] == "Alt+N"
        assert len(second.shortcuts()) == 6
    finally:
        second.close()


def test_entries_follow_creation_order_and_edit_keeps_position(repo):
    first = repo.save(WordEntry("zebra", [Sense("n.", "斑马")]))
    second = repo.save(WordEntry("apple", [Sense("n.", "苹果")]))
    first.text = "zebras"
    repo.save(first)
    assert [entry.id for entry in repo.list_entries()] == [first.id, second.id]


"""Cache policy through the shared committed-change interface."""
from copy import deepcopy

import pytest

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.list_state import ListState
from vocabulary.list_sync import CommittedChange, LocationMap, synchronize


@pytest.mark.parametrize("field,requery", [("text", True), ("translation", True),
    ("phonetic", False), ("pos", False), ("important", False), ("reorder", False)])
def test_content_changes_only_invalidate_queries_whose_results_can_change(repo, field, requery):
    before = repo.save(WordEntry("apple", [Sense("n.", "fruit"), Sense("v.", "grow")],
                                [RelatedWord("child", [Sense("n.", "small")])]))
    state = ListState()
    normal = state.apply(repo.query_page("pending"))
    search = state.apply(repo.query_page("pending", "apple"))
    state.eye_visible["pending"] = True
    normal.revealed_ids.add(before.id)
    after = deepcopy(before)
    if field == "translation":
        after.senses[0].translation = "new"
    elif field == "pos":
        after.senses[0].pos = "adj."
    elif field == "reorder":
        after.senses.reverse()
    else:
        setattr(after, field, not after.important if field == "important" else "changed")
    synchronize(state, CommittedChange(before=before, after=after))
    assert normal.valid
    assert search.valid != requery
    assert normal.entries[0] == after and search.entries[0] == after
    assert normal.entries[0] is not search.entries[0]
    assert normal.revealed_ids == {before.id} and state.eye_visible["pending"]
    search.valid = False
    synchronize(state, CommittedChange(before=after, after=after))
    assert not search.valid


def test_deleted_off_page_word_invalidates_totals_even_without_resident_copy(repo):
    repo.import_entries([WordEntry(str(i), [Sense("n.", "meaning")]) for i in range(501)])
    state = ListState()
    pending = state.apply(repo.query_page("pending"))
    learned = state.apply(repo.query_page("learned"))
    removed = repo.query_page("pending", page_number=2).entries[0]
    assert removed.id not in pending.ids
    synchronize(state, CommittedChange(before=removed))
    assert not pending.valid and not learned.valid


def test_removed_targets_cancel_deferred_edit_but_preserve_word_selection():
    entry = WordEntry("word", id=7)
    change = CommittedChange(before=entry, after=entry,
        locations=LocationMap(owners={0: None, 1: 0}, senses={-1: {0: None, 1: 0}}))
    for target in ((7, 0, 0, 1), (7, -1, 0, 3)):
        assert change.target(target) == (7, -1, 0, 1)
        assert change.target(target, editing=True) is None
    assert change.target((7, 1, 0, 3)) == (7, 0, 0, 3)
    assert change.target((7, -1, 1, 3)) == (7, -1, 0, 3)


def test_identity_mapping_preserves_blank_word_name_cells():
    locations = LocationMap()
    for owner in (-1, 0, 1):
        for sense in (0, 1, 2):
            for column in range(5):
                part = (7, owner, sense, column)
                assert locations.target(part) == part

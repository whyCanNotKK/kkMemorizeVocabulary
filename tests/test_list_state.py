from types import SimpleNamespace

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.list_state import ListState


def result(category="pending", keyword="", page=1, ids=(1, 2), total=1001):
    return SimpleNamespace(
        category=category, keyword=keyword, page_number=page, total=total,
        counts={"all": total, "pending": total, "learned": 0},
        entries=[WordEntry(f"word-{value}", [Sense("n.", "translation")], id=value)
                 for value in ids], before_id=None, after_id=None)


def test_normal_pages_replace_history_but_search_is_independent():
    state = ListState()
    first = state.apply(result())
    state.set_revealed("pending", 1, True)
    first.scroll = 123
    first.view_state = ((1, -1, 0, 1), True, 0)
    state.apply(result("all"))
    assert state.normal["all"].revealed_ids == set()
    search = state.apply(result(keyword="word", ids=(1,)))
    assert search.revealed_ids == {1}
    assert state.normal["pending"] is first
    assert first.revealed_ids == {1}
    assert first.scroll == 123
    state.apply(result(keyword="other", ids=(20,)))
    assert state.search.ids == {20}
    state.apply(result(page=2, ids=(501, 502)))
    assert state.normal["pending"].revealed_ids == set()
    state.apply(result())
    assert state.normal["pending"].revealed_ids == set()


def test_refresh_preserves_existing_words_and_initializes_only_newcomers():
    state = ListState()
    state.apply(result())
    state.set_revealed("pending", 1, True)
    state.eye_visible["pending"] = True
    refreshed = state.apply(result(ids=(1, 2, 3)))
    assert refreshed.revealed_ids == {1, 3}
    state.eye_visible["pending"] = False
    refreshed = state.apply(result(ids=(2, 3, 4)))
    assert refreshed.revealed_ids == {3}


def test_eye_defaults_change_only_for_manual_uniform_visibility():
    state = ListState()
    assert not any(state.eye_visible.values())
    state.apply(result())
    state.set_revealed("pending", 1, True)
    assert not state.eye_visible["pending"]
    state.set_revealed("pending", 2, True)
    assert state.eye_visible["pending"]
    state.set_revealed("pending", 1, False)
    assert state.eye_visible["pending"]
    state.set_revealed("pending", 2, False, manual=False)
    assert state.eye_visible["pending"]
    state.update_eye_from_visible("pending", set(), set())
    assert state.eye_visible["pending"]
    state.set_revealed("pending", 2, False)
    assert not state.eye_visible["pending"]
    assert state.toggle_visibility("pending")
    assert state.normal["pending"].revealed_ids == {1, 2}


def test_content_patches_do_not_alias_preview_or_visibility_between_caches():
    state = ListState()
    normal = state.apply(result())
    other = state.apply(result("all"))
    search = state.apply(result(keyword="word"))
    state.set_revealed("pending", 1, True)
    changed = WordEntry("new spelling", [Sense("v.", "new translation")], id=1)
    state.patch_entry(changed)
    changed.senses[0].translation = "outside mutation"
    normal.entries[0].senses[0].translation = "inline preview"
    assert other.entries[0].senses[0].translation == "new translation"
    assert search.entries[0].senses[0].translation == "new translation"
    assert other.revealed_ids == set()
    assert normal.revealed_ids == {1}
    state.patch_sort_orders({1: 77})
    assert all(cache.entries[0].sort_order == 77 for cache in (normal, other, search))


def test_invalidated_snapshots_keep_view_metadata_and_empty_results_are_reusable():
    state = ListState()
    page = state.apply(result())
    page.scroll = 29
    state.invalidate(("pending",), search=False)
    assert state.cache_for("pending") is None
    assert page.scroll == 29
    assert state.bump_revision() == 1
    empty = state.apply(result(page=None, total=0, ids=()))
    assert empty.page_count == 0
    assert state.cache_for("pending", page_number=1) is empty


def test_shared_search_positions_store_no_old_word_pages():
    state = ListState()
    search = state.apply(result("all", keyword="WORD", page=2, ids=(501,)))
    search.scroll = 41
    state.remember_search_position()
    state.apply(result("pending", keyword="WORD", ids=(1,)))
    assert state.search_positions["all"].scroll == 41
    assert state.search_positions["all"].page_number == 2
    assert state.search.ids == {1}
    state.reset_search_positions("changed", except_category="pending")
    assert state.search_positions["all"].keyword == "changed"
    assert state.search_positions["all"].page_number == 1


def test_shared_search_cache_restores_matching_category_page_position():
    state = ListState()
    search = state.apply(result("all", keyword="word", page=2, ids=(501, 502)))
    search.view_state = ((502, 0, 1, 3), True, 123)
    search.scroll = 123
    state.remember_search_position()
    pending = state.apply(result("pending", keyword="word", ids=(1, 2)))
    pending.view_state = ((2, -1, 0, 1), True, 37)
    pending.scroll = 37
    state.remember_search_position()

    restored = state.apply(result("all", keyword="word", page=2, ids=(501, 502)))

    assert state.search is restored
    assert restored.view_state == ((502, 0, 1, 3), True, 123)
    assert restored.scroll == 123
    assert state.search_positions["pending"].view_state == ((2, -1, 0, 1), True, 37)
    assert state.search_positions["pending"].scroll == 37


def test_search_position_from_another_keyword_is_not_restored_even_for_same_ids():
    state = ListState()
    old = state.apply(result("all", keyword="old", page=2, ids=(501, 502)))
    old.view_state = ((502, -1, 0, 3), True, 129)
    old.scroll = 129
    state.remember_search_position()
    state.apply(result("pending", keyword="new"))

    restored = state.apply(result("all", keyword="new", page=2, ids=(501, 502)))

    assert restored.view_state == (None, False, 0)
    assert restored.scroll == 0
    assert state.search_positions["all"].keyword == "new"


def test_search_positions_reset_discards_old_selection_when_query_returns():
    state = ListState()
    old = state.apply(result("all", keyword="word", page=2, ids=(501, 502)))
    old.view_state = ((502, -1, 0, 1), True, 83)
    old.scroll = 83
    state.remember_search_position()
    state.reset_search_positions("changed")
    state.apply(result("pending", keyword="changed"))
    state.reset_search_positions("word")

    restored = state.apply(result("all", keyword="word", page=2, ids=(501, 502)))

    assert restored.view_state == (None, False, 0)
    assert restored.scroll == 0


def test_related_reorder_remaps_current_inactive_and_search_locations_by_index():
    state = ListState()
    normal = state.apply(result())
    inactive = state.apply(result("all"))
    search = state.apply(result(keyword="word"))
    normal.entries[0].related = [RelatedWord("duplicate"), RelatedWord("duplicate")]
    normal.view_state = ((1, 0, 2, 3), True, 17)
    normal.scroll = 19
    inactive.view_state = ((1, 1, 0, 1), False, 23)
    search.view_state = ((1, 1, 1, 2), True, 31)
    state.search_positions["learned"].view_state = ((1, 0, 0, 1), True, 41)
    state.search_positions["learned"].scroll = 43
    state.search_positions["all"].view_state = ((1, -1, 0, 3), True, 47)
    state.normal["learned"].view_state = ((2, 0, 0, 3), True, 53)

    state.remap_locations(1, related_permutation=[1, 0])

    assert normal.view_state == ((1, 1, 2, 3), True, 17)
    assert normal.scroll == 19
    assert inactive.view_state == ((1, 0, 0, 1), False, 23)
    assert search.view_state == ((1, 0, 1, 2), True, 31)
    assert state.search_positions["learned"].view_state == ((1, 1, 0, 1), True, 41)
    assert state.search_positions["learned"].scroll == 43
    assert state.search_positions["all"].view_state == ((1, -1, 0, 3), True, 47)
    assert state.normal["learned"].view_state == ((2, 0, 0, 3), True, 53)
    assert normal.entries[0].related == [RelatedWord("duplicate"), RelatedWord("duplicate")]


def test_sense_reorder_only_remaps_matching_original_owner_even_when_related_moves():
    state = ListState()
    current = state.apply(result())
    inactive = state.apply(result("all"))
    current.view_state = ((1, 1, 1, 3), True, 5)
    inactive.view_state = ((1, 0, 1, 2), False, 7)
    state.search_positions["all"].view_state = ((1, 1, 0, 3), True, 11)
    state.search_positions["learned"].view_state = ((1, -1, 1, 3), True, 13)

    state.remap_locations(1, related_permutation=[1, 0],
                          sense_owner=1, sense_permutation=[1, 0])

    assert current.view_state == ((1, 0, 0, 3), True, 5)
    assert inactive.view_state == ((1, 1, 1, 2), False, 7)
    assert state.search_positions["all"].view_state == ((1, 0, 1, 3), True, 11)
    assert state.search_positions["learned"].view_state == ((1, -1, 1, 3), True, 13)


def test_main_sense_location_remapping_is_independent_of_content_patch():
    state = ListState()
    current = state.apply(result())
    current.view_state = ((1, -1, 2, 3), True, 71)
    state.search_positions["all"].view_state = ((1, -1, 0, 2), False, 73)
    duplicate = Sense("n.", "same definition")
    updated = WordEntry("word-1", [duplicate, duplicate, duplicate], id=1)
    state.patch_entry(updated)
    assert current.view_state == ((1, -1, 2, 3), True, 71)

    state.remap_locations(1, sense_owner=-1, sense_permutation=[2, 0, 1])

    assert current.view_state == ((1, -1, 0, 3), True, 71)
    assert state.search_positions["all"].view_state == ((1, -1, 1, 2), False, 73)
    assert state.normal["learned"].view_state == (None, False, 0)
    assert current.entries[0] == updated

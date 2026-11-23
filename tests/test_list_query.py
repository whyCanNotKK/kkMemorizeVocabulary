"""SQL pagination reads only visible aggregates and preserves search semantics."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import sqlite3

import pytest

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary import list_query
from vocabulary.list_query import PAGE_SIZE, PageReader
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository


def add(repo, name, translation="", related=(), learned=False):
    return repo.save(WordEntry(name, [Sense("n.", translation)], list(related), learned=learned))


def expected_search(entries, keyword):
    keyword = keyword.strip().casefold()
    found = []
    for entry in entries:
        fields = ((entry.text,), (word.text for word in entry.related),
                  (sense.translation for sense in entry.senses),
                  (sense.translation for word in entry.related for sense in word.senses))
        for priority, texts in enumerate(fields):
            positions = [text.casefold().find(keyword) for text in texts]
            positions = [position for position in positions if position >= 0]
            if positions:
                found.append(((priority, min(positions)), entry.id))
                break
    found.sort(key=lambda item: item[0])
    return [entry_id for _, entry_id in found]


def test_empty_page_has_no_invented_page(repo):
    for keyword in ("", "missing"):
        page = repo.query_page("pending", keyword)
        assert page.entries == [] and page.page_number is None
        assert page.total == page.page_count == 0
        assert page.before_id is page.after_id is page.anchor_id is None
        assert page.counts == {"pending": 0, "all": 0, "learned": 0}


def test_page_is_500_complete_groups_and_fetches_no_other_aggregates(repo, monkeypatch):
    entries = [add(repo, f"word-{i}", f"meaning-{i}",
                   [RelatedWord(f"child-{i}", [Sense("v.", "one"), Sense("adj.", "two")])])
               for i in range(PAGE_SIZE + 3)]
    fetched = []
    original = list_query.get_entries_by_ids

    def load(connection, ids):
        fetched.append(list(ids))
        return original(connection, ids)

    monkeypatch.setattr(list_query, "get_entries_by_ids", load)
    first = repo.query_page("pending")
    assert len(first.entries) == PAGE_SIZE
    assert first.page_count == 2 and first.total == PAGE_SIZE + 3
    assert first.before_id is None and first.after_id == entries[PAGE_SIZE].id
    assert fetched[-1] == [entry.id for entry in entries[:PAGE_SIZE]]
    assert first.entries[-1].related[0].senses == [Sense("v.", "one"), Sense("adj.", "two")]
    last = repo.query_page("pending", page_number=999)
    assert last.page_number == 2 and len(last.entries) == 3
    assert last.before_id == entries[PAGE_SIZE - 1].id and last.after_id is None
    assert fetched[-1] == [entry.id for entry in entries[PAGE_SIZE:]]


def test_counts_category_order_anchor_and_small_page_sizes(repo):
    learned = add(repo, "learned", learned=True)
    first, second, third = [add(repo, name) for name in ("first", "second", "third")]
    page = repo.query_page("all", page_size=2, anchor_id=learned.id)
    assert page.counts == {"pending": 3, "all": 4, "learned": 1}
    assert [entry.id for entry in page.entries] == [third.id, learned.id]
    assert page.page_number == page.page_count == 2
    assert page.anchor_id == learned.id and page.before_id == second.id
    pending = repo.query_page("pending", anchor_id=learned.id, page_size=2)
    assert pending.anchor_id is None and pending.page_number == 1
    assert [entry.id for entry in pending.entries] == [first.id, second.id]
    assert pending.after_id == third.id


@pytest.mark.parametrize("keyword", ["val", " VAL ", "strasse", "ss", "苹果", "a b", "%", "_"])
def test_sql_search_matches_existing_ranking_and_literal_substrings(repo, keyword):
    add(repo, "revival", "val")
    add(repo, "valid", "")
    add(repo, "parent", "val", [RelatedWord("xval", [Sense("v.", "val")]),
                                 RelatedWord("val-child", [Sense("n.", "")])])
    add(repo, "translated", "xval")
    add(repo, "related-translation", "", [RelatedWord("child", [Sense("v.", "xxval"),
                                                               Sense("n.", "val")])])
    add(repo, "Straße", "苹果 a b % _")
    add(repo, "not-a-cross-field-match", "a", [RelatedWord("only-b", [Sense("n.", "b")])])
    add(repo, "learned-val", learned=True)
    expected = expected_search(repo.list_entries(), keyword)
    exported = repo.export_entries("all", keyword)
    assert [entry.id for entry in exported] == expected
    ids = []
    for number in range(1, (len(expected) + 1) // 2 + 1):
        page = repo.query_page("all", keyword, page_number=number, page_size=2)
        assert page.total == len(expected)
        assert page.keyword == keyword.strip().casefold()
        ids.extend(entry.id for entry in page.entries)
    assert ids == expected


def test_search_anchor_and_outside_neighbors_use_global_search_rank(repo):
    entries = [add(repo, f"val-{i}") for i in range(7)]
    page = repo.query_page("all", "VAL", page_size=2, anchor_id=entries[4].id)
    assert page.page_number == 3 and page.page_count == 4
    assert [entry.id for entry in page.entries] == [entries[4].id, entries[5].id]
    assert page.before_id == entries[3].id and page.after_id == entries[6].id
    assert page.anchor_id == entries[4].id
    missing = repo.query_page("all", "absent", anchor_id=entries[4].id)
    assert missing.anchor_id is None and missing.total == 0


def test_page_aggregate_and_counts_share_one_snapshot(repo, monkeypatch):
    saved = add(repo, "snapshot", "original")
    other = Repository(repo.path)
    original = list_query.get_entries_by_ids

    def concurrent_change(connection, ids):
        other.set_phonetic(saved.id, "changed")
        add(other, "new-word")
        return original(connection, ids)

    monkeypatch.setattr(list_query, "get_entries_by_ids", concurrent_change)
    try:
        page = repo.query_page("all")
        assert page.total == 1 and page.entries[0].phonetic == ""
        assert repo.get(saved.id).phonetic == "changed"
    finally:
        other.close()


def test_page_reader_is_readonly_and_owned_by_worker(repo):
    add(repo, "readonly")

    def read():
        reader = PageReader(repo.path)
        try:
            result = reader.query_page("pending", "read")
            with pytest.raises(sqlite3.OperationalError):
                reader.connection.execute("DELETE FROM words")
            return result
        finally:
            reader.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(read).result().total == 1


def test_sorting_uses_only_metadata_and_returns_changed_orders(repo, monkeypatch):
    entries = [add(repo, str(i)) for i in range(6)]
    monkeypatch.setattr(repo, "get", lambda *_: pytest.fail("sort must not hydrate a word"))
    monkeypatch.setattr(repo, "list_entries", lambda: pytest.fail("sort must not load the list"))
    changed = repo.move_entry(entries[4].id, 1)
    assert changed == {entries[4].id: 2, entries[1].id: 3, entries[2].id: 4, entries[3].id: 5}
    assert [row[0] for row in repo.connection.execute("SELECT id FROM words ORDER BY sort_order,id")] == [
        entries[i].id for i in (0, 4, 1, 2, 3, 5)]
    assert repo.move_entry(entries[4].id, 1) == {}
    assert isinstance(repo.shuffle_entries("pending"), dict)


def test_sort_failure_rolls_back_delta_and_high_water(repo):
    entries = [add(repo, str(i)) for i in range(4)]
    before = list(repo.connection.execute("SELECT id,sort_order FROM words ORDER BY id"))
    maximum = repo.max_pending_sort_order
    repo.connection.execute("""CREATE TRIGGER reject_move BEFORE UPDATE OF sort_order ON words
        WHEN NEW.id=2 BEGIN SELECT RAISE(ABORT,'reject'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.move_entry(entries[3].id, 0)
    assert [tuple(row) for row in repo.connection.execute("SELECT id,sort_order FROM words ORDER BY id")] == [
        tuple(row) for row in before]
    assert repo.max_pending_sort_order == maximum


def test_study_reuses_cached_content_and_batches_only_missing_words(repo, monkeypatch):
    entries = [add(repo, f"study-{i}") for i in range(4)]
    context = StudyContext(date(2026, 11, 22), False)
    cached = {entries[0].id: entries[0]}
    original = repo.get_entries_by_ids
    fetched = []

    def load(ids):
        ids = list(ids)
        fetched.append(ids)
        return original(ids)

    monkeypatch.setattr(repo, "get", lambda *_: pytest.fail("study must batch aggregates"))
    monkeypatch.setattr(repo, "get_entries_by_ids", load)
    tasks = repo.prepare_study(context, entries_by_id=cached)
    assert len(tasks) == 4 and set(fetched[-1]) == {entry.id for entry in entries[1:]}
    assert next(task.entry for task in tasks if task.entry.id == entries[0].id) is entries[0]
    assert set(cached) == {entry.id for entry in entries}
    fetched.clear()
    repo.study_tasks(context, entries_by_id=cached)
    assert fetched == []


def test_content_revision_tracks_only_successful_content_writes(repo):
    assert repo.content_revision == 0
    saved = add(repo, "revision", related=[RelatedWord("child", [Sense("v.", "meaning")])])
    assert repo.content_revision == 1
    for operation in (
            lambda: repo.set_flag(saved.id, "important", False),
            lambda: repo.set_flag(saved.id, "learned", True),
            lambda: repo.set_phonetic(saved.id, "phonetic"),
            lambda: repo.sync_import_translation(saved.id, Sense("n.", "updated")),
            lambda: repo.append_import_related(saved.id, RelatedWord("other", [Sense("n.", "other")])),
            lambda: repo.move_related(saved.id, 0, 1),
            lambda: repo.batch_update_sort_orders({saved.id: 50}),
    ):
        before = repo.content_revision
        operation()
        assert repo.content_revision > before
    before = repo.content_revision
    repo.query_page("all", "rev")
    repo.get_entries_by_ids([saved.id])
    repo.export_entries("all")
    assert repo.content_revision == before


def test_study_and_settings_writes_do_not_advance_content_revision(repo):
    saved = add(repo, "study-revision")
    context = StudyContext(date(2026, 11, 22), False)
    before = repo.content_revision
    repo.prepare_study(context)
    repo.start_study_task(context)
    repo.save_study_duration(context, 30)
    repo.complete_study(saved.id, context, duration_seconds=31)
    repo.write_shortcuts(repo.shortcuts())
    repo.write_study_draw_settings(repo.study_draw_settings())
    repo.clear_content("delete study record")
    assert repo.content_revision == before


def test_content_revision_does_not_advance_on_failed_write_or_noop(repo):
    entries = [add(repo, str(i)) for i in range(4)]
    before = repo.content_revision
    repo.move_entry(entries[0].id, 0)
    repo.batch_update_sort_orders({})
    repo.set_flag(entries[0].id, "learned", False)
    assert repo.content_revision == before
    repo.connection.execute("""CREATE TRIGGER reject_revision BEFORE UPDATE OF sort_order ON words
        WHEN NEW.id=2 BEGIN SELECT RAISE(ABORT,'reject'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.move_entry(entries[3].id, 0)
    assert repo.content_revision == before
    with pytest.raises(ValueError):
        repo.save(WordEntry("", [Sense("n.", "invalid")]))
    assert repo.content_revision == before


def test_clear_content_revision_changes_only_when_words_are_removed(repo):
    add(repo, "cleared")
    before = repo.content_revision
    repo.clear_content("remove all")
    assert repo.content_revision > before
    before = repo.content_revision
    repo.clear_content("remove all")
    assert repo.content_revision == before

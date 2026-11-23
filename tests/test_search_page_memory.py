"""Category search positions survive replacement of the single search content cache."""
from PySide6.QtCore import Qt

from vocabulary.domain import Sense, WordEntry


def seed_pages(repo):
    repo.import_entries([
        WordEntry(f"word-{number:04}", [Sense("n.", "definition")], learned=number >= 600)
        for number in range(650)
    ])


def test_returning_to_search_category_restores_page_selection_and_scroll(window, repo):
    seed_pages(repo)
    window.set_filter("all")
    window.search_box.setText("word")
    window.change_page(2)
    assert window._display_cache.page_number == 2
    table = window.table
    selected = window._display_cache.entries[20].id
    table.select_entry(selected, scroll=True)
    bar = table.verticalScrollBar()
    bar.setValue(bar.value() + 8)
    view, scroll = table.view_state(), bar.value()
    assert scroll > 0

    window.set_filter("pending")
    assert window._display_cache.category == "pending"
    assert window.list_state.search.category == "pending"
    window.set_filter("all")

    assert window._display_cache.page_number == 2
    assert table.view_state()[:2] == view[:2]
    assert bar.value() == scroll
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == selected
    assert all(not row.hidden for row in window.model.rows)


def test_reopening_cached_search_cannot_restore_other_category_old_position(
        window, repo, monkeypatch):
    seed_pages(repo)
    window.set_filter("all")
    window.search_box.setText("word")
    window.change_page(2)
    window.set_filter("pending")
    assert window.list_state.search_positions["all"].page_number == 2
    window.search_box.clear()
    calls = []
    read = window.list_jobs.reader.query_page

    def tracked(*args, **kwargs):
        calls.append((args, kwargs))
        return read(*args, **kwargs)

    monkeypatch.setattr(window.list_jobs.reader, "query_page", tracked)
    window.search_box.setText("word")

    assert not calls  # The shared pending search page is still available.
    position = window.list_state.search_positions["all"]
    assert position.keyword == "word" and position.page_number == 1
    assert position.view_state == (None, False, 0)
    assert position.scroll == 0
    window.set_filter("all")
    assert window._display_cache.page_number == 1
    assert not window.table.selectionModel().hasSelection()


def test_reopening_cached_search_without_selection_starts_at_top(window, repo):
    seed_pages(repo)
    window.search_box.setText("word")
    window.table.verticalScrollBar().setValue(200)
    window.table.clearSelection()
    window.table.setCurrentIndex(window.model.index(-1, -1))
    window.search_box.clear()
    window.search_box.setText("WORD")
    assert window.table.verticalScrollBar().value() == 0
    assert not window.table.selectionModel().hasSelection()

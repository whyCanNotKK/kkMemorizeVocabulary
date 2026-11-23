"""Real worker-thread reads, debounce and bounded caches through MainWindow."""
from datetime import date
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.list_query import PageReader
from vocabulary.transfer import parse_entries
from vocabulary.window import MainWindow


def seed(repo, count=3):
    repo.import_entries([WordEntry(f"word-{number:04}", [Sense("n.", "definition")])
                         for number in range(count)])
    return [row[0] for row in repo.connection.execute("SELECT id FROM words ORDER BY sort_order,id")]


def settled(qtbot, window, *, category=None, keyword=None, page=None):
    def ready():
        cache = window._display_cache
        return (cache is not None and not window._list_loading
                and (category is None or cache.category == category)
                and (keyword is None or cache.keyword == keyword)
                and (page is None or cache.page_number == page))
    qtbot.waitUntil(ready, timeout=5000)


@pytest.fixture
def make_window(qtbot, themed_app, repo):
    windows = []

    def make():
        value = MainWindow(repo)
        windows.append(value)
        qtbot.addWidget(value)
        value.show()
        value.activateWindow()
        # The initial form-focus singleShot must finish before testing a focus
        # handoff from the search box or a deferred editor.
        qtbot.wait(30)
        settled(qtbot, value)
        return value

    yield make
    for value in windows:
        value._closing = True
        value._search_timer.stop()
        value.table.delegate.cancel()
        value.study_page.table.delegate.cancel()
        value._baseline = value.form_value()
        value.list_jobs.shutdown()
        value.study_page.timer.dispose()
        value.hide()


def test_initial_window_loads_only_first_500_and_page_changes_are_real_queries(
        make_window, repo, qtbot):
    ids = seed(repo, 502)
    window = make_window()
    assert window._display_cache.total == 502
    assert [entry.id for entry in window._display_cache.entries] == ids[:500]
    assert len(window.model.rows) == 500
    assert all(row.hidden for row in window.model.rows)
    window.change_page(2)
    settled(qtbot, window, page=2)
    assert [entry.id for entry in window._display_cache.entries] == ids[500:]
    assert window.pagination.page_count == 2
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == ids[500]


def test_debounce_and_early_enter_share_the_latest_query_intent(
        make_window, repo, qtbot, monkeypatch):
    seed(repo)
    calls = []
    original = PageReader.query_page

    def read(reader, category, keyword="", *args, **kwargs):
        calls.append(keyword)
        return original(reader, category, keyword, *args, **kwargs)

    monkeypatch.setattr(PageReader, "query_page", read)
    window = make_window()
    calls.clear()
    window.search_box.setFocus()
    window.search_box.setText("word-0001")
    qtbot.wait(60)
    assert calls == [] and window._search_timer.isActive()
    qtbot.keyClick(window.search_box, Qt.Key.Key_Return)
    settled(qtbot, window, keyword="word-0001", page=1)
    assert calls == ["word-0001"]
    assert window.table.hasFocus()
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.text == "word-0001"
    assert all(not row.hidden for row in window.model.rows)


def test_late_old_query_cannot_replace_new_results(
        make_window, repo, qtbot, monkeypatch):
    seed(repo)
    started, release = Event(), Event()
    original = PageReader.query_page

    def read(reader, category, keyword="", *args, **kwargs):
        result = original(reader, category, keyword, *args, **kwargs)
        if keyword == "word-0000":
            started.set()
            release.wait(timeout=2)
        return result

    monkeypatch.setattr(PageReader, "query_page", read)
    window = make_window()
    window.search_box.setText("word-0000")
    qtbot.waitUntil(started.is_set, timeout=2000)
    window.search_box.setText("word-0002")
    qtbot.wait(180)
    release.set()
    settled(qtbot, window, keyword="word-0002")
    qtbot.wait(30)
    assert [entry.text for entry in window._display_cache.entries] == ["word-0002"]
    assert window.search_box.text() == "word-0002"


def test_search_cache_and_eye_are_isolated_from_normal_page(
        make_window, repo, qtbot):
    ids = seed(repo)
    window = make_window()
    window.reveal(ids[0], True)
    normal = window.list_state.normal["pending"]
    assert normal.revealed_ids == {ids[0]}
    assert not window.list_state.eye_visible["pending"]
    window.search_box.setText("word-0001")
    settled(qtbot, window, keyword="word-0001")
    assert window.global_hide_button.isHidden()
    assert all(not row.hidden for row in window.model.rows)
    assert window.list_state.normal["pending"] is normal
    assert normal.revealed_ids == {ids[0]}
    window.table.select_entry(ids[1])
    window.table.setFocus()
    qtbot.keyClick(window.table, Qt.Key.Key_Space)
    window.set_global_hidden(True)
    assert all(not row.hidden for row in window.model.rows)
    assert normal.revealed_ids == {ids[0]}
    window.search_box.clear()
    settled(qtbot, window, keyword="")
    assert not window.global_hide_button.isHidden()
    assert {row.entry.id for row in window.model.rows if not row.hidden} == {ids[0]}
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == ids[1]


def test_failed_search_keeps_draft_but_export_uses_last_committed_query(
        make_window, repo, qtbot, monkeypatch, tmp_path):
    seed(repo)
    original = PageReader.query_page

    def read(reader, category, keyword="", *args, **kwargs):
        if keyword == "unavailable":
            raise RuntimeError("injected read failure")
        return original(reader, category, keyword, *args, **kwargs)

    monkeypatch.setattr(PageReader, "query_page", read)
    window = make_window()
    window.search_box.setText("word-0000")
    settled(qtbot, window, keyword="word-0000")
    old_cache = window._display_cache
    failures = []
    window.list_jobs.failure.connect(lambda *_: failures.append(True))
    notices = []
    monkeypatch.setattr(window, "show_notice", lambda *args, **kwargs: notices.append(args))
    window.search_box.setText("unavailable")
    qtbot.waitUntil(lambda: bool(failures), timeout=2000)
    assert not window._list_loading and window.table.isEnabled()
    assert window._display_cache is old_cache
    assert window.search_box.text() == "unavailable" and notices == []
    output = tmp_path / "export.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    window.export_words()
    assert [entry.text for entry in parse_entries(output.read_text(encoding="utf-8"))] == ["word-0000"]


def test_last_word_f4_selects_global_next_word_that_fills_page(
        make_window, repo, qtbot):
    ids = seed(repo, 502)
    window = make_window()
    window.table.select_entry(ids[499])
    window.table.setFocus()
    qtbot.keyClick(window.table, Qt.Key.Key_F4)
    qtbot.waitUntil(lambda: not window._list_loading and window._display_cache.total == 501,
                    timeout=5000)
    assert repo.get(ids[499]).learned
    assert window._display_cache.page_number == 1
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == ids[500]
    assert window._display_cache.entries[-1].id == ids[500]


def test_three_reorders_write_without_reloading_any_list(
        make_window, repo, qtbot, monkeypatch):
    entry = repo.save(WordEntry("first", [Sense("n.", "one"), Sense("v.", "two")],
                               [RelatedWord("child-a", [Sense("n.", "a")]),
                                RelatedWord("child-b", [Sense("n.", "b")])]))
    seed(repo, 2)
    window = make_window()
    attempted_reads = []

    def no_read(*args, **kwargs):
        attempted_reads.append((args, kwargs))
        raise AssertionError("a reorder must not reload a word list")

    monkeypatch.setattr(PageReader, "query_page", no_read)
    monkeypatch.setattr(repo, "query_page", no_read)
    monkeypatch.setattr(repo, "list_entries", no_read)
    window.move_word(entry.id, 1)
    window.move_related_word(entry.id, 0, 1)
    window.move_word_sense(entry.id, -1, 0, 1)
    qtbot.wait(30)
    assert not window._list_loading
    assert attempted_reads == []
    assert window._display_cache.entries[1].id == entry.id
    saved = repo.get(entry.id)
    assert [word.text for word in saved.related] == ["child-b", "child-a"]
    assert [sense.pos for sense in saved.senses] == ["v.", "n."]
    resident = next(value for value in window._display_cache.entries if value.id == entry.id)
    assert resident.related == saved.related and resident.senses == saved.senses


def test_normal_tab_caches_and_study_content_are_independent(
        make_window, repo, qtbot, monkeypatch):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 1), False)))
    ids = seed(repo)
    calls = []
    original = PageReader.query_page

    def read(reader, category, *args, **kwargs):
        calls.append(category)
        return original(reader, category, *args, **kwargs)

    monkeypatch.setattr(PageReader, "query_page", read)
    window = make_window()
    window.reveal(ids[0], True)
    window.set_filter("all")
    settled(qtbot, window, category="all")
    assert all(row.hidden for row in window.model.rows)
    window.reveal(ids[1], True)
    calls.clear()
    window.set_filter("pending")
    settled(qtbot, window, category="pending")
    assert calls == []
    assert {row.entry.id for row in window.model.rows if not row.hidden} == {ids[0]}
    window.set_filter("study17")
    assert window.filter_name == "study17" and window.pagination.isHidden()
    assert window.study_page.tasks
    first_task = window.study_page.tasks[0].entry
    normal_entry = next(entry for entry in window.list_state.normal["pending"].entries
                        if entry.id == first_task.id)
    assert first_task is not normal_entry
    window.set_global_hidden(False)
    assert window.list_state.eye_visible["study17"]
    assert not window.list_state.eye_visible["pending"]
    window.set_filter("pending")
    settled(qtbot, window, category="pending")
    assert {row.entry.id for row in window.model.rows if not row.hidden} == {ids[0]}


def test_search_tab_waits_for_requery_before_editing_logical_next_word(
        make_window, repo, qtbot):
    ids = seed(repo)
    window = make_window()
    window.search_box.setText("word")
    settled(qtbot, window, keyword="word")
    assert window.table.edit_field((ids[0], -1, 0, 3))
    editor = window.table.delegate.editor
    editor.setPlainText("updated translation")
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    qtbot.waitUntil(lambda: not window._list_loading and window.table.delegate.editor is not None,
                    timeout=3000)
    row = window.table.delegate.edit_index.data(Qt.ItemDataRole.UserRole)
    assert row.entry.id == ids[1]
    assert window.table.delegate.edit_index.column() == 1
    assert repo.get(ids[0]).senses[0].translation == "updated translation"


@pytest.mark.parametrize("action", ["filter", "page"])
def test_saving_search_editor_does_not_cancel_requested_filter_or_page(
        make_window, repo, qtbot, action):
    ids = seed(repo, 501 if action == "page" else 3)
    window = make_window()
    window.search_box.setText("word")
    settled(qtbot, window, keyword="word", page=1)
    assert window.table.edit_field((ids[0], -1, 0, 3))
    window.table.delegate.editor.setPlainText("saved before leaving")
    if action == "filter":
        assert window.set_filter("all")
        settled(qtbot, window, category="all", keyword="word")
    else:
        window.change_page(2)
        settled(qtbot, window, category="pending", keyword="word", page=2)
    # The old editor's queued 0ms flush must not replace the explicit action.
    qtbot.wait(60)
    assert not window._list_loading
    assert window._display_cache.category == ("all" if action == "filter" else "pending")
    assert window._display_cache.page_number == (1 if action == "filter" else 2)
    assert repo.get(ids[0]).senses[0].translation == "saved before leaving"


def test_clearing_search_reuses_normal_cache_with_selected_word_and_visibility(
        make_window, repo, qtbot, monkeypatch):
    ids = seed(repo)
    window = make_window()
    window.reveal(ids[0], True)
    normal = window.list_state.normal["pending"]
    window.search_box.setText("word-0001")
    settled(qtbot, window, keyword="word-0001")
    window.table.select_entry(ids[1])
    calls = []
    original = PageReader.query_page

    def read(reader, *args, **kwargs):
        calls.append((args, kwargs))
        return original(reader, *args, **kwargs)

    monkeypatch.setattr(PageReader, "query_page", read)
    window.search_box.clear()
    settled(qtbot, window, keyword="")
    qtbot.wait(30)
    assert calls == []
    assert window._display_cache is normal
    assert {row.entry.id for row in window.model.rows if not row.hidden} == {ids[0]}
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == ids[1]


def test_saving_matching_new_word_during_debounce_keeps_saved_target_and_form_focus(
        make_window, repo, qtbot):
    seed(repo, 2)
    window = make_window()
    block = window.blocks[0]
    block.word.setText("word-new")
    block.rows[0].pos.set_pos("n.")
    block.rows[0].translation.setPlainText("new definition")
    window.search_box.setFocus()
    window.search_box.setText("word")
    assert window._search_timer.isActive()
    block.word.setFocus()
    assert window._save_form()
    settled(qtbot, window, keyword="word")
    qtbot.wait(220)  # The superseded debounce callback must not steal focus later.
    assert not window._search_timer.isActive()
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.text == "word-new"
    assert window.blocks[0].word.hasFocus()
    assert window.search_box.text() == "word"


@pytest.mark.parametrize("cancel_action", ["query", "filter", "failure"])
def test_deferred_search_edit_does_not_open_after_its_intent_is_canceled(
        make_window, repo, qtbot, monkeypatch, cancel_action):
    ids = seed(repo)
    hold_next, started, release = Event(), Event(), Event()
    original = PageReader.query_page

    def read(reader, *args, **kwargs):
        result = original(reader, *args, **kwargs)
        if hold_next.is_set():
            hold_next.clear()
            started.set()
            release.wait(timeout=2)
            if cancel_action == "failure":
                raise RuntimeError("injected deferred-edit read failure")
        return result

    monkeypatch.setattr(PageReader, "query_page", read)
    window = make_window()
    window.search_box.setText("word")
    settled(qtbot, window, keyword="word")
    assert window.table.edit_field((ids[0], -1, 0, 3))
    editor = window.table.delegate.editor
    editor.setPlainText("saved before deferred navigation")
    hold_next.set()
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    qtbot.waitUntil(started.is_set, timeout=2000)
    assert window._pending_edit_target is not None
    if cancel_action == "query":
        window.search_box.setText("word-0002")
    elif cancel_action == "filter":
        assert window.set_filter("all")
    release.set()
    if cancel_action == "query":
        settled(qtbot, window, keyword="word-0002")
    elif cancel_action == "filter":
        settled(qtbot, window, category="all", keyword="word")
    else:
        qtbot.waitUntil(lambda: not window._list_loading, timeout=2000)
    qtbot.wait(30)
    assert window._pending_edit_target is None
    assert window.table.delegate.editor is None
    assert repo.get(ids[0]).senses[0].translation == "saved before deferred navigation"


def test_study_search_shortcut_focuses_search_after_real_async_all_tab_load(
        make_window, repo, qtbot, monkeypatch):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 1), False)))
    seed(repo)
    window = make_window()
    assert window.set_filter("study17")
    window.study_page.table.setFocus()
    qtbot.keyClick(window.study_page.table, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, window, category="all", keyword="")
    qtbot.waitUntil(window.search_box.hasFocus, timeout=2000)
    assert window.filter_name == "all"
    assert not window.search_box.isHidden()



def test_second_page_drag_uses_category_offset_and_keeps_first_page_unchanged(make_window, repo, qtbot):
    ids = seed(repo, 1002)
    window = make_window()
    window.change_page(2)
    settled(qtbot, window, page=2)
    window.move_word(ids[501], 0)
    assert not window._list_loading
    assert window._display_cache.page_number == 2
    assert [entry.id for entry in window._display_cache.entries[:2]] == [ids[501], ids[500]]
    assert [entry.id for entry in repo.query_page('pending', page_number=1).entries] == ids[:500]
    assert [entry.id for entry in repo.query_page('pending', page_number=2).entries[:2]] == [ids[501], ids[500]]

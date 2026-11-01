from datetime import date

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture(params=["pending", "all", "learned", "study17"])
def page(window, repo, monkeypatch, request):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 1), False)))
    entries = [repo.save(WordEntry(
        f"word-{i}", [Sense("n.", "释义"), Sense("v.", "另一释义")],
        [RelatedWord("child", [Sense("n.", "关联释义")])],
        learned=request.param == "learned")) for i in range(2)]
    window.set_filter(request.param)
    table = window.study_page.table if request.param == "study17" else window.table
    return table, entries


def test_eye_hides_words_revealed_individually(page, window, qtbot):
    table, entries = page
    for entry in entries:
        table.revealRequested.emit(entry.id, True)
    assert all(not row.hidden for row in table.model().rows)

    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)

    assert len(table.model().rows) == len(entries)
    assert all(row.hidden for row in table.model().rows)
    assert window.global_hide_button.isChecked()


def test_eye_shows_words_hidden_individually(page, window, qtbot):
    table, entries = page
    window.set_global_hidden(False)
    for entry in entries:
        table.revealRequested.emit(entry.id, False)

    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)

    assert all(not row.hidden for row in table.model().rows)
    assert not window.global_hide_button.isChecked()


def test_mixed_visibility_uses_last_eye_operation(page, window, qtbot):
    table, entries = page
    table.revealRequested.emit(entries[0].id, True)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert all(not row.hidden for row in table.model().rows)
    assert not window.global_hidden

    table.revealRequested.emit(entries[0].id, False)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert all(row.hidden for row in table.model().rows)
    assert window.global_hidden


def test_eye_records_are_independent_including_overlapping_categories(window, repo, qtbot):
    entries = [repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义")])) for i in range(2)]
    window.refresh()
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert not window.global_hidden

    window.set_filter("all")
    assert window.global_hidden and window.global_hide_button.isChecked()
    window.reveal(entries[0].id, False)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert all(not row.hidden for row in window.model.rows)

    window.set_global_hidden(True)
    window.reveal(entries[0].id, True)
    window.set_filter("pending")
    assert not window.global_hidden and not window.global_hide_button.isChecked()
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert all(row.hidden for row in window.model.rows)


@pytest.mark.parametrize("page", ["study17"], indirect=True)
def test_study_eye_does_not_change_ordinary_views_and_reverse(page, window):
    table, entries = page
    window.set_global_hidden(False)
    window.set_filter("pending")
    assert window.global_hidden and all(row.hidden for row in window.model.rows)
    window.set_global_hidden(False)
    window.set_global_hidden(True)
    window.set_filter("study17")
    assert not window.global_hidden and not window.global_hide_button.isChecked()
    assert all(not row.hidden for row in table.model().rows)


@pytest.mark.parametrize("filter_name", ["pending", "all", "learned", "study17"])
def test_empty_page_eye_toggles_record(window, qtbot, monkeypatch, filter_name):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 1), True)))
    window.set_filter(filter_name)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert not window.global_hidden
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert window.global_hidden


def test_search_eye_uses_actual_results_and_only_changes_matching_words(window, repo, qtbot):
    matching = repo.save(WordEntry("matching", [Sense("n.", "匹配")]))
    other = repo.save(WordEntry("other", [Sense("n.", "其他")]))
    window.reveal(other.id, True)
    window.search_box.setText("matching")
    assert all(not row.hidden for row in window.model.rows)

    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert window.model.rows[0].hidden
    assert window.revealed == {other.id}
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert all(not row.hidden for row in window.model.rows)
    assert window.revealed == {matching.id, other.id}
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    window.search_box.clear()
    assert {row.entry.id for row in window.model.rows if row.hidden} == {matching.id}


def test_eye_includes_offscreen_words_and_preserves_selection_and_scroll(window, repo, qtbot):
    entries = [repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义")])) for i in range(40)]
    window.refresh()
    for entry in entries:
        window.revealed.add(entry.id)
    window.refresh()
    window.table.select_entry(entries[0].id)
    bar = window.table.verticalScrollBar()
    bar.setValue(200)
    position = bar.value()
    assert position > 0 and bar.maximum() > position

    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)

    assert all(row.hidden for row in window.model.rows)
    assert bar.value() == position
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[0].id
    assert window.table.selectionModel().hasSelection()


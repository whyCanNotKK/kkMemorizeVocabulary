from datetime import date

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.shortcuts import SettingsDialog


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
    assert not window.global_hide_button.isChecked()


def test_eye_shows_words_hidden_individually(page, window, qtbot):
    table, entries = page
    window.set_global_hidden(False)
    for entry in entries:
        table.revealRequested.emit(entry.id, False)

    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)

    assert all(not row.hidden for row in table.model().rows)
    assert window.global_hide_button.isChecked()


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
    assert window.global_hidden and not window.global_hide_button.isChecked()
    window.reveal(entries[0].id, False)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert all(not row.hidden for row in window.model.rows)

    window.set_global_hidden(True)
    window.reveal(entries[0].id, True)
    window.set_filter("pending")
    assert not window.global_hidden and window.global_hide_button.isChecked()
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
    assert not window.global_hidden and window.global_hide_button.isChecked()
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


def test_search_hides_eye_and_preserves_normal_page_mixed_visibility(window, repo, qtbot):
    matching = repo.save(WordEntry("matching", [Sense("n.", "匹配")]))
    other = repo.save(WordEntry("other", [Sense("n.", "其他")]))
    window.refresh()
    window.reveal(other.id, True)
    window.search_box.setText("matching")
    assert all(not row.hidden for row in window.model.rows)
    assert window.global_hide_button.isHidden()
    window.set_global_hidden(True)
    assert not window.model.rows[0].hidden
    assert window.revealed == {other.id}
    window.set_global_hidden(False)
    assert all(not row.hidden for row in window.model.rows)
    assert window.revealed == {other.id}
    window.search_box.clear()
    assert {row.entry.id for row in window.model.rows if row.hidden} == {matching.id}
    assert window.global_hide_button.isVisible()


@pytest.mark.parametrize("gesture", ["space", "ctrl-click"])
@pytest.mark.parametrize("filter_name", ["pending", "all", "learned"])
def test_search_individual_hide_gestures_leave_group_shown(window, repo, qtbot, gesture, filter_name):
    entry = repo.save(WordEntry("matching", [Sense("n.", "definition")],
                               [RelatedWord("child", [Sense("v.", "related")])],
                               learned=filter_name == "learned"))
    window.set_filter(filter_name)
    window.search_box.setText("matching")
    table = window.table
    table.select_entry(entry.id)
    table.setFocus()
    assert table.hasFocus()
    assert all(not row.hidden for row in window.model.rows)

    if gesture == "space":
        qtbot.keyClick(table, Qt.Key.Key_Space)
    else:
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.ControlModifier,
                        pos=table.visualRect(window.model.index(1, 3)).center())

    assert len(window.model.rows) == 2 and all(not row.hidden for row in window.model.rows)
    assert table.hasFocus()
    if gesture == "space":
        qtbot.keyClick(table, Qt.Key.Key_Space)
    else:
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.ControlModifier,
                        pos=table.visualRect(window.model.index(0, 3)).center())
    assert len(window.model.rows) == 2 and all(not row.hidden for row in window.model.rows)
    assert table.hasFocus()
    assert not window.revealed
    window.search_box.clear()
    assert len(window.model.rows) == 1 and window.model.rows[0].hidden


@pytest.mark.parametrize("button", [Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton])
def test_search_never_creates_mask_after_global_hide(window, repo, qtbot, button):
    entry = repo.save(WordEntry("matching", [Sense("n.", "definition")]))
    window.search_box.setText("matching")
    window.set_global_hidden(True)
    assert not window.model.rows[0].hidden
    table = window.table
    index = window.model.index(0, 3)
    table.setCurrentIndex(index)

    qtbot.mouseClick(table.viewport(), button, pos=table.visualRect(index).center())

    assert not window.model.rows[0].hidden
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id
    table.delegate.cancel()


def test_search_space_respects_focus_and_refresh_keeps_normal_state_separate(window, repo, qtbot):
    entries = [repo.save(WordEntry(name, [Sense("n.", "definition")]))
               for name in ("match-one", "match-two", "other")]
    window.refresh()
    window.reveal(entries[1].id, True)
    window.search_box.setText("match")
    window.search_box.setFocus()
    qtbot.keyClick(window.search_box, Qt.Key.Key_Space)
    assert window.search_box.text() == "match "
    assert all(not row.hidden for row in window.model.rows)
    qtbot.keyClick(window.search_box, Qt.Key.Key_Return)
    assert window.table.hasFocus()
    qtbot.keyClick(window.table, Qt.Key.Key_Space)

    window.refresh()
    assert all(not row.hidden for row in window.model.rows)
    assert window.list_state.normal["pending"].revealed_ids == {entries[1].id}
    window.search_box.setText("match-")
    assert all(not row.hidden for row in window.model.rows)
    qtbot.keyClick(window.table, Qt.Key.Key_Space)
    qtbot.keyClick(window.table, Qt.Key.Key_Space)
    window.search_box.clear()
    assert {row.entry.id for row in window.model.rows if not row.hidden} == {entries[1].id}


def test_eye_includes_offscreen_words_and_preserves_selection_and_scroll(window, repo, qtbot):
    entries = [repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义")])) for i in range(40)]
    window.refresh()
    window.set_global_hidden(False)
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


def test_visibility_shortcut_and_button_share_toggle_on_every_page(page, window, qtbot):
    table, entries = page
    window.setFocus()
    assert window.global_hide_button.toolTip() == "显示/隐藏 本页释义 · Ctrl+S"
    qtbot.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert all(not row.hidden for row in table.model().rows)
    assert window.global_hide_button.isChecked()
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier, "", True)
    from PySide6.QtWidgets import QApplication
    QApplication.sendEvent(window, repeat)
    assert all(not row.hidden for row in table.model().rows)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert all(row.hidden for row in table.model().rows)
    assert window.global_hide_button.toolTip() == "显示/隐藏 本页释义 · Ctrl+S"


def test_visibility_shortcut_settings_remap_and_restore(page, window, qtbot):
    table, entries = page
    window.shortcuts.set_suspended(True)
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.record("toggle_visibility")
    dialog.capture("toggle_visibility", "Alt+E")
    assert dialog.editors["toggle_visibility"].text() == "Alt+E"
    assert window.global_hide_button.toolTip() == "显示/隐藏 本页释义 · Alt+E"
    window.shortcuts.set_suspended(False)
    window.setFocus()
    qtbot.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert all(row.hidden for row in table.model().rows)
    qtbot.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.AltModifier)
    assert all(not row.hidden for row in table.model().rows)
    dialog.restore()
    assert window.global_hide_button.toolTip() == "显示/隐藏 本页释义 · Ctrl+S"
    qtbot.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert all(row.hidden for row in table.model().rows)


def test_visibility_shortcut_preserves_bottom_draft_and_search_scope(window, repo, qtbot):
    matching = repo.save(WordEntry("matching", [Sense("n.", "匹配")]))
    other = repo.save(WordEntry("other", [Sense("n.", "其他")]))
    window.refresh()
    window.reveal(other.id, True)
    window.search_box.setText("matching")
    window.blocks[0].word.setText("draft")
    window.blocks[0].word.setFocus()
    draft = window.form_value()
    qtbot.keyClick(window.blocks[0].word, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert all(not row.hidden for row in window.model.rows)
    assert window.form_value() == draft and window.is_dirty()
    assert window.revealed == {other.id} and len(repo.list_entries()) == 2
    window.search_box.clear()
    assert {row.entry.id for row in window.model.rows if row.hidden} == {matching.id}


def test_visibility_shortcut_blocks_on_invalid_cell_and_when_suspended(window, repo, qtbot):
    entry = repo.save(WordEntry("word", [Sense("n.", "释义")],
                                [RelatedWord("child", [Sense("n.", "child")])]))
    window.refresh()
    window.reveal(entry.id, True)
    window.table.edit(window.model.index(0, 1))
    editor = window.table.delegate.editor
    editor.setText("")
    qtbot.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert window.table.delegate.editor is editor
    assert all(not row.hidden for row in window.model.rows)
    assert repo.get(entry.id).senses[0].translation == "释义"
    window.table.delegate.cancel()
    window.setFocus()
    window.shortcuts.set_suspended(True)
    qtbot.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert all(not row.hidden for row in window.model.rows)
    window.shortcuts.set_suspended(False)


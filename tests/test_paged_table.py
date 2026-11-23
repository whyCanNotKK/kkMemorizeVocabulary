"""Loaded-page interactions run entirely against in-memory aggregates."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QHeaderView, QToolTip

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.table import WordTable, WordTableModel
from vocabulary.theme import icon


@pytest.fixture
def loaded_table(qtbot, themed_app):
    model = WordTableModel(lambda *_: True)
    table = WordTable(model, lambda: True)
    qtbot.addWidget(table)
    table.resize(1000, 500)
    table.set_page_state("pending", False, 2)
    table.show()
    entries = [WordEntry(f"word-{i}", [Sense("n.", f"definition-{i}")], id=i + 1)
               for i in range(3)]
    table.replace_entries(entries, {entry.id for entry in entries}, preserve=False)
    qtbot.wait(20)
    return table, entries


def test_search_cannot_hide_via_space_control_click_or_direct_request(loaded_table, qtbot):
    table, entries = loaded_table
    table.set_page_state("pending", True, 3)
    # Even a caller passing a hidden projection cannot hide a search result.
    table.replace_entries(entries, set())
    changes = []
    table.revealRequested.connect(lambda *args: changes.append(args))
    table.select_entry(entries[0].id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Space)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.ControlModifier,
                    table.visualRect(table.model().index(0, 3)).center())
    table.set_entry_revealed(entries[0].id, False)
    assert changes == []
    assert all(not row.hidden for row in table.model().rows)


def test_search_last_enter_loops_without_requesting_global_hide(loaded_table, qtbot):
    table, entries = loaded_table
    table.set_page_state("pending", True, 3)
    cycles = []
    table.entryCycleRestartRequested.connect(lambda: cycles.append(True))
    table.select_entry(entries[-1].id)
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Return)
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[0].id
    assert cycles == []
    assert all(not row.hidden for row in table.model().rows)


def test_search_translation_tooltip_preserves_edit_and_copy_without_hide(
        loaded_table, monkeypatch):
    table, _ = loaded_table
    table.set_page_state("pending", True, 3)
    index = table.model().index(0, 3)
    rect = table.visualRect(index)
    point = rect.topLeft() + QPoint(16, table.model().rows[0].top_padding + 8)
    assert table.delegate.copy_hit(index, point)
    tips = []
    monkeypatch.setattr(QToolTip, "showText", lambda _point, text, *_: tips.append(text))
    event = QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point))
    QApplication.sendEvent(table.viewport(), event)
    assert tips and "复制" in tips[-1] and "编辑" in tips[-1]
    assert "隐藏释义" not in tips[-1] and "空格" not in tips[-1]


def test_replace_group_resizes_only_changed_group_and_retains_other_selection(
        loaded_table, monkeypatch):
    table, entries = loaded_table
    table.select_entry(entries[2].id)
    untouched_height = table.rowHeight(1)
    measured = []
    original = table.delegate.sizeHint

    def measure(option, index):
        measured.append(index.data(Qt.ItemDataRole.UserRole).entry.id)
        return original(option, index)

    monkeypatch.setattr(table.delegate, "sizeHint", measure)
    updated = replace(entries[0], senses=[Sense("n.", "changed\nsecond line")],
                      related=[RelatedWord("child", [Sense("v.", "child translation")])])
    table.replace_entry(updated)
    assert len(table.model().rows) == 4
    assert measured and set(measured) == {entries[0].id}
    assert table.rowHeight(2) == untouched_height
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[2].id
    table.replace_entry(updated, False)
    assert len(table.model().rows) == 3 and table.model().rows[0].hidden
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[2].id


def test_repeated_row_measurement_reuses_text_layout_and_width_invalidates_it(
        loaded_table, monkeypatch):
    table, _ = loaded_table
    import vocabulary.table as module
    built = []
    original = module.text_document

    def build(text, font, width):
        built.append((text, width))
        return original(text, font, width)

    monkeypatch.setattr(module, "text_document", build)
    table.delegate._documents.clear()
    table.delegate._heights.clear()
    table.delegate._measure_document = None
    table.update_spans()
    table.update_spans()
    assert len(built) == 1  # one scratch document, independent of word count
    scratch = table.delegate._measure_document
    table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    table.setColumnWidth(3, table.columnWidth(3) - 50)
    table.update_spans()
    assert table.delegate._measure_document is scratch and len(built) == 1
    import math
    for number, row in enumerate(table.model().rows):
        fresh = original(row.word.senses[row.sense].translation, table.font(), table.columnWidth(3) - 24)
        expected = max(row.minimum_height, math.ceil(fresh.size().height()) + row.top_padding + row.bottom_padding)
        assert table.rowHeight(number) == expected


def test_expanding_selected_group_selects_all_inserted_rows(loaded_table):
    table, entries = loaded_table
    table.select_entry(entries[0].id)
    updated = replace(entries[0], related=[RelatedWord("child", [Sense("n.", "related")])])
    table.replace_entry(updated)
    selected = {index.row() for index in table.selectionModel().selectedIndexes()}
    assert selected == {0, 1}
    table.replace_entry(updated, False)
    assert {index.row() for index in table.selectionModel().selectedIndexes()} == {0}


def test_partial_projection_clears_spelling_and_mouse_edit_target(loaded_table):
    table, entries = loaded_table
    table.select_entry(entries[0].id)
    table.study_input.feed(entries[0].id, entries[0].text, "w")
    table.note_mouse_position(table._last_mouse_position + QPoint(1, 0))
    assert table.study_input.position == 1 and table._mouse_moved
    table.replace_entry(entries[0], False)
    assert table.study_input.position == 0 and not table._mouse_moved
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[0].id


def test_closed_eye_has_distinct_rendered_shape(themed_app):
    opened = icon("eye").pixmap(24, 24).toImage()
    closed = icon("eye_closed").pixmap(24, 24).toImage()
    assert not closed.isNull() and opened != closed


def test_edit_is_deferred_when_preparation_starts_async_reload(loaded_table):
    table, entries = loaded_table
    target = (entries[1].id, -1, 0, 3)
    deferred = []
    table.editDeferred.connect(deferred.append)

    def loading(*_):
        table.setEnabled(False)

    table.editPreparationRequested.connect(loading)
    assert table.edit_field(target) is False
    assert deferred == [target] and table.delegate.editor is None
    table.editPreparationRequested.disconnect(loading)
    # The result has a different order: the logical target, not the old row,
    # must determine which word gets edited.
    table.replace_entries(list(reversed(entries)), {entry.id for entry in entries})
    table.setEnabled(True)
    assert table.edit_field(target) is True
    assert table.delegate.edit_index.data(Qt.ItemDataRole.UserRole).entry.id == entries[1].id
    table.delegate.cancel()
    table.replace_entries([entries[0]], {entries[0].id})
    assert table.edit_field(target) is False
    assert table.delegate.editor is None


def test_pointer_editor_uses_same_async_preparation_guard(loaded_table):
    table, entries = loaded_table
    deferred = []
    table.editDeferred.connect(deferred.append)
    table.editPreparationRequested.connect(lambda *_: table.setEnabled(False))
    table._edit_pointer_field(table.model().index(0, 3))
    assert deferred == [(entries[0].id, -1, 0, 3)]
    assert table.delegate.editor is None


def test_backtab_keeps_phonetic_focus_after_deferred_edit(loaded_table):
    table, entries = loaded_table
    target = (entries[0].id, -1, 0, 1)
    assert table.edit_field((entries[0].id, -1, 0, 2))
    deferred = []
    table.editDeferred.connect(deferred.append)

    def loading(*_):
        table.setEnabled(False)

    table.editPreparationRequested.connect(loading)
    table.move_editor(-1)
    assert deferred == [target] and table.delegate.editor is None
    table.editPreparationRequested.disconnect(loading)
    table.setEnabled(True)
    assert table.edit_field(target)
    assert table.delegate.editor.phonetic.hasFocus()
    table.delegate.cancel()

from datetime import date

import pytest
from PySide6.QtCore import QPoint, Qt

from vocabulary.domain import Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.window import MainWindow


def drag_boundary(qtbot, table, column, delta):
    header = table.horizontalHeader()
    surface = header.viewport()
    origin = QPoint(header.sectionViewportPosition(column) + header.sectionSize(column) - 1,
                    header.height() // 2)
    qtbot.mousePress(surface, Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(surface, origin + QPoint(delta, 0), delay=20)
    qtbot.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=origin + QPoint(delta, 0))
    qtbot.wait(20)


@pytest.mark.parametrize("page", ["pending", "all", "learned", "study17"])
def test_only_allowed_boundaries_resize_and_survive_refresh(window, repo, qtbot, monkeypatch, page):
    entry = repo.save(WordEntry("word", [Sense("n.", "很长的翻译 " * 45)], learned=page == "learned"))
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter(page)
    table = window.current_table()
    if page == "study17":
        table.set_entry_revealed(entry.id, True)
    else:
        window.reveal(entry.id, True)
    qtbot.wait(20)
    original = [table.columnWidth(i) for i in range(5)]
    for column in (0, 2):
        drag_boundary(qtbot, table, column, 50)
        assert [table.columnWidth(i) for i in range(5)] == original
    drag_boundary(qtbot, table, 1, 70)
    assert table.columnWidth(1) > original[1] + 60
    assert table.columnWidth(0) == original[0] and table.columnWidth(2) == original[2]
    before_height, before_translation = table.rowHeight(0), table.columnWidth(3)
    # Move the translation/action divider left, leaving less room for wrapped text.
    drag_boundary(qtbot, table, 3, -100)
    assert table.columnWidth(3) < before_translation - 80
    assert table.columnWidth(4) > original[4] + 80
    assert table.rowHeight(0) > before_height
    widths = [table.columnWidth(i) for i in range(5)]
    if page == "study17":
        assert window.study_page.load(window.study_page.context)
    else:
        window.refresh()
    qtbot.wait(20)
    assert [table.columnWidth(i) for i in range(5)] == widths


@pytest.fixture(params=["pending", "all", "learned", "study17"])
def resize_list(request, window, repo, qtbot, monkeypatch):
    entry = repo.save(WordEntry("word", [Sense("n.", "很长的翻译 " * 20)],
                               learned=request.param == "learned"))
    for number in range(24):
        repo.save(WordEntry(f"filler-{number}", [Sense("n.", "译文")],
                           learned=request.param == "learned"))
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter(request.param)
    table = window.current_table()
    table.revealRequested.emit(entry.id, True)
    qtbot.wait(30)
    return table, entry, request.param


def edge(table, column):
    header = table.horizontalHeader()
    return header.sectionViewportPosition(column) + header.sectionSize(column)


def double_boundary(qtbot, table, column, button=Qt.MouseButton.LeftButton):
    header = table.horizontalHeader()
    point = QPoint(edge(table, column) - 1, header.height() // 2)
    qtbot.mouseClick(header.viewport(), button, pos=point)
    qtbot.mouseDClick(header.viewport(), button, pos=point)
    qtbot.mouseRelease(header.viewport(), button, pos=point)
    qtbot.wait(20)


def assert_fits(table):
    assert table.columnWidth(1) >= 34
    assert table.columnWidth(3) >= 80
    assert table.columnWidth(4) >= table.minimum_action_width()
    assert sum(table.columnWidth(i) for i in range(5)) <= table.viewport().width()
    assert table.horizontalScrollBar().maximum() == 0


def test_drag_stays_in_viewport_and_does_not_move_other_divider(resize_list, qtbot):
    table, entry, page = resize_list
    fixed = table.columnWidth(0), table.columnWidth(2)
    right = edge(table, 3)
    drag_boundary(qtbot, table, 1, 5000)
    assert_fits(table)
    assert edge(table, 3) == right and table.columnWidth(3) == 80
    left = edge(table, 1)
    drag_boundary(qtbot, table, 3, -5000)
    assert_fits(table)
    assert edge(table, 1) == left
    drag_boundary(qtbot, table, 1, -5000)
    assert table.columnWidth(1) == 34
    drag_boundary(qtbot, table, 3, -5000)
    assert_fits(table)
    assert table.columnWidth(3) == 80
    assert (table.columnWidth(0), table.columnWidth(2)) == fixed


def test_double_click_resets_only_requested_divider(resize_list, repo, qtbot):
    table, entry, page = resize_list
    changes = repo.connection.total_changes
    table.select_entry(entry.id)
    drag_boundary(qtbot, table, 1, 60)
    drag_boundary(qtbot, table, 3, -80)
    right = edge(table, 3)
    double_boundary(qtbot, table, 1)
    assert table.columnWidth(1) == 206 and edge(table, 3) == right
    left = edge(table, 1)
    double_boundary(qtbot, table, 3)
    assert table.columnWidth(4) == table.minimum_action_width()
    assert edge(table, 1) == left
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id
    assert repo.connection.total_changes == changes
    assert_fits(table)


def test_partial_reset_then_full_reset_when_room_returns(resize_list, qtbot):
    table, entry, page = resize_list
    drag_boundary(qtbot, table, 1, -5000)
    target = table.columnWidth(0) + 120 + table.columnWidth(2) + 80
    drag_boundary(qtbot, table, 3, target - edge(table, 3))
    right = edge(table, 3)
    double_boundary(qtbot, table, 1)
    assert table.columnWidth(1) == 120 and edge(table, 3) == right
    double_boundary(qtbot, table, 3)
    assert table.columnWidth(1) == 120
    double_boundary(qtbot, table, 1)
    assert table.columnWidth(1) == 206
    assert_fits(table)


def test_locked_boundaries_and_header_text_never_reset(resize_list, qtbot):
    table, entry, page = resize_list
    drag_boundary(qtbot, table, 1, 60)
    drag_boundary(qtbot, table, 3, -70)
    widths = [table.columnWidth(i) for i in range(5)]
    for column in (0, 2):
        double_boundary(qtbot, table, column)
        assert [table.columnWidth(i) for i in range(5)] == widths
    header = table.horizontalHeader()
    qtbot.mouseDClick(header.viewport(), Qt.MouseButton.LeftButton,
                      pos=QPoint(header.sectionViewportPosition(1) + 30, header.height() // 2))
    qtbot.mouseDClick(header.viewport(), Qt.MouseButton.LeftButton,
                      pos=QPoint(header.viewport().width() - 3, header.height() // 2))
    double_boundary(qtbot, table, 1, Qt.MouseButton.RightButton)
    double_boundary(qtbot, table, 3, Qt.MouseButton.RightButton)
    assert [table.columnWidth(i) for i in range(5)] == widths


def test_resize_and_reset_preserve_selection_and_pixel_scroll(resize_list, qtbot):
    table, entry, page = resize_list
    table.select_entry(entry.id)
    bar = table.verticalScrollBar()
    bar.setValue(120)
    position = bar.value()
    for column, delta in ((1, 40), (3, -50)):
        drag_boundary(qtbot, table, column, delta)
        assert bar.value() == position
        assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id
    double_boundary(qtbot, table, 1)
    assert bar.value() == position
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id


def test_window_shrink_priority_and_expansion_do_not_restore_old_widths(resize_list, window, qtbot):
    table, entry, page = resize_list
    window.resize(1400, 820)
    qtbot.wait(30)
    drag_boundary(qtbot, table, 1, 350)
    drag_boundary(qtbot, table, 3, -200)
    word, actions = table.columnWidth(1), table.columnWidth(4)
    window.resize(1300, 820)
    qtbot.wait(30)
    assert (table.columnWidth(1), table.columnWidth(4)) == (word, actions)
    window.resize(860, 820)
    qtbot.wait(40)
    assert_fits(table)
    assert table.columnWidth(4) == table.minimum_action_width()
    available = table.viewport().width() - table.columnWidth(0) - table.columnWidth(2) - 80
    assert table.columnWidth(1) == min(word, available - table.minimum_action_width())
    reduced = table.columnWidth(1), table.columnWidth(4)
    window.resize(1400, 820)
    qtbot.wait(30)
    assert (table.columnWidth(1), table.columnWidth(4)) == reduced
    assert_fits(table)


def test_reset_widths_survive_refresh_and_tabs_but_new_window_uses_defaults(resize_list, window, repo, qtbot):
    table, entry, page = resize_list
    drag_boundary(qtbot, table, 1, 60)
    drag_boundary(qtbot, table, 3, -80)
    double_boundary(qtbot, table, 1)
    widths = [table.columnWidth(i) for i in range(5)]
    other = "pending" if page == "study17" else "study17"
    window.set_filter(other)
    window.set_filter(page)
    qtbot.wait(30)
    assert [table.columnWidth(i) for i in range(5)] == widths
    fresh = MainWindow(repo)
    qtbot.addWidget(fresh)
    fresh.show()
    fresh.set_filter(page)
    qtbot.wait(30)
    assert fresh.current_table().columnWidth(1) == 206
    assert fresh.current_table().columnWidth(4) == table.minimum_action_width()
    fresh._closing = True
    fresh.hide()


def test_invalid_inline_edit_blocks_drag_and_reset(resize_list, qtbot):
    table, entry, page = resize_list
    drag_boundary(qtbot, table, 1, 60)
    index = table._entry_index(entry.id, column=1)
    table.scrollTo(index)
    table.edit(index)
    editor = table.delegate.editor
    editor.clear()
    widths = [table.columnWidth(i) for i in range(5)]
    for column in (1, 3):
        drag_boundary(qtbot, table, column, -50)
        double_boundary(qtbot, table, column)
        assert [table.columnWidth(i) for i in range(5)] == widths
        assert table.delegate.editor is editor and not editor.text()

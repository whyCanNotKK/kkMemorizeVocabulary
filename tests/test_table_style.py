"""Verify word ownership cues through real table rendering and interactions."""
from datetime import date

import pytest
from PySide6.QtCore import QPoint, Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture(params=["pending", "all", "learned", "study17"])
def styled_list(window, repo, qtbot, monkeypatch, request):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 1), False)))
    learned = request.param == "learned"
    entry = repo.save(WordEntry("record", [Sense("n.", "记录"), Sense("v.", "记录")], [
        RelatedWord("recorder", [Sense("n.", "录音机\n记录员"), Sense("n.", "竖笛")]),
        RelatedWord("recording", [Sense("n.", "录音"), Sense("n.", "记录的过程")]),
        RelatedWord("recordable", [Sense("adj.", "可记录的")]),
    ], learned=learned))
    hidden = repo.save(WordEntry("hidden", [Sense("n.", "隐藏译文")], learned=learned))
    window.set_filter(request.param)
    window.set_form_collapsed(True)
    table = window.study_page.table if request.param == "study17" else window.table
    table.revealRequested.emit(entry.id, True)
    table.clearSelection()
    qtbot.wait(20)
    return table, entry, hidden


def color_at(image, point):
    return image.pixelColor(point).name()


def cell(table, row, column):
    return table.visualRect(table.model().index(row, column))


@pytest.mark.parametrize("width", [860, 1200])
def test_uniform_background_and_word_rails(styled_list, window, qtbot, width):
    table, entry, hidden = styled_list
    window.resize(width, 820)
    qtbot.wait(20)
    image = table.viewport().grab().toImage()
    for number, row in enumerate(table.model().rows):
        if row.entry.id != entry.id:
            continue
        expected = "#1e242d"
        for column, inset in ((1, 150), (2, None), (3, None)):
            rect = cell(table, number, column)
            x = rect.left() + inset if inset else rect.right() - 2
            assert color_at(image, QPoint(x, rect.center().y())) == expected
        # A rail crosses sense-row boundaries, but ends before the next word.
        rail_x = cell(table, number, 3).left() + 5
        assert color_at(image, QPoint(rail_x, cell(table, number, 3).top() + row.top_padding + 2)) == "#70869c"
        if row.sense > 0:
            y = cell(table, number, 3).top()
            for offset in (-1, 0):
                assert color_at(image, QPoint(rail_x, y + offset)) == "#70869c"
        elif row.owner >= 0:
            assert color_at(image, QPoint(rail_x, cell(table, number, 3).top() + 3)) != "#70869c"

    # The group's footer and hidden word share the ordinary list background.
    last = cell(table, 6, 1)
    assert color_at(image, QPoint(last.left() + 150, last.bottom() - 2)) == "#1e242d"
    hidden_index = next(i for i, row in enumerate(table.model().rows) if row.entry.id == hidden.id)
    assert color_at(image, cell(table, hidden_index, 1).center()) == "#1e242d"
    assert table.model().rows[hidden_index].hidden


def test_uniform_group_selection_without_edge_stripe(styled_list, qtbot):
    table, entry, hidden = styled_list
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=cell(table, 3, 3).center())
    image = table.viewport().grab().toImage()
    for number, row in enumerate(table.model().rows):
        if row.entry.id != entry.id:
            continue
        expected = "#283440"
        rect = cell(table, number, 1)
        assert color_at(image, QPoint(rect.left() + 150, rect.center().y())) == expected
        assert color_at(image, QPoint(1, rect.center().y())) == expected

    hidden_index = next(i for i, row in enumerate(table.model().rows) if row.entry.id == hidden.id)
    mask = cell(table, hidden_index, 2)
    point = mask.topLeft() + QPoint(40, 18)
    assert color_at(image, point) == "#788493"
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert all(not row.hidden for row in table.model().rows)
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == hidden.id


def test_rail_remains_continuous_when_word_start_scrolls_offscreen(styled_list, qtbot):
    table, entry, _ = styled_list
    # Make the table short enough to scroll into the first related word.
    table.setFixedHeight(170)
    qtbot.wait(20)
    table.verticalScrollBar().setValue(table.rowViewportPosition(3) - 4)
    image = table.viewport().grab().toImage()
    rect = cell(table, 3, 3)
    assert rect.top() >= 0
    x = rect.left() + 5
    assert color_at(image, QPoint(x, rect.top())) == "#70869c"
    assert color_at(image, QPoint(x, rect.top() - 1)) == "#70869c"
    assert color_at(image, QPoint(cell(table, 3, 1).left() + 150, rect.top())) == "#1e242d"


@pytest.mark.parametrize("styled_list", ["pending"], indirect=True)
def test_group_style_follows_the_dragged_group(styled_list, qtbot):
    table, entry, _ = styled_list
    table.select_entry(entry.id)
    origin = dict(table.action_rects(cell(table, 0, 4)))["drag"].center()
    destination = origin + QPoint(0, 45)
    qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(table.viewport(), destination, delay=20)
    assert table._drag_active
    image = table.viewport().grab().toImage()
    rect = cell(table, 2, 1)
    assert color_at(image, QPoint(rect.left() + 150, rect.center().y() + 45)) == "#283440"
    rail = cell(table, 3, 3)
    assert color_at(image, QPoint(rail.left() + 5, rail.top() + 45)) == "#9fb9d0"
    qtbot.keyClick(table, Qt.Key.Key_Escape)
    assert not table._drag_active

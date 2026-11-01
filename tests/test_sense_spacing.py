from datetime import date

import pytest

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


def translation_ink_bounds(table, image, row):
    rect = table.visualRect(table.model().index(row, 3)).adjusted(12, 0, -12, 0)
    # Restrict to the translation, excluding selection, borders and the word column.
    ys = [y for y in range(rect.top(), rect.bottom() + 1)
          if any((color := image.pixelColor(x, y)).red() > 180
                 and color.green() > 180 and color.blue() > 180
                 for x in range(rect.left(), rect.right() + 1))]
    assert ys, f"No translation rendered in row {row}"
    return min(ys), max(ys)


@pytest.mark.parametrize("page", ["pending", "all", "learned", "study17"])
@pytest.mark.parametrize("translation", ["相同释义", "相同释义\n第二行", "自动折行释义" * 10])
def test_equal_gaps_between_senses(window, repo, qtbot, monkeypatch, page, translation):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 1), False)))
    senses = [Sense(pos, translation) for pos in ("n.", "v.", "adj.")]
    entry = repo.save(WordEntry("example", senses,
                               [RelatedWord("related", senses), RelatedWord("another", senses)],
                               learned=page == "learned"))
    window.set_filter(page)
    window.set_form_collapsed(True)
    table = window.study_page.table if page == "study17" else window.table
    table.revealRequested.emit(entry.id, True)
    qtbot.wait(20)
    image = table.viewport().grab().toImage()
    bounds = [translation_ink_bounds(table, image, row) for row in range(9)]
    inner_gaps = []
    for first in (0, 3, 6):
        gaps = [bounds[i + 1][0] - bounds[i][1] - 1 for i in range(first, first + 2)]
        geometry = [(table.rowHeight(row), table.model().rows[row].top_padding)
                    for row in range(first, first + 3)]
        assert gaps[0] == gaps[1], f"{page}, first row {first}: gaps {gaps}, (height, top) {geometry}"
        inner_gaps.extend(gaps)
    assert len(set(inner_gaps)) == 1
    word_gaps = [bounds[first][0] - bounds[first - 1][1] - 1 for first in (3, 6)]
    assert word_gaps[0] == word_gaps[1] > inner_gaps[0]
    action_cell = table.visualRect(table.model().index(0, 4))
    assert all(action_cell.contains(target) for _, target in table.action_rects(action_cell))

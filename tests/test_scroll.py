from datetime import date

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


def many_words(repo, count=80):
    return [repo.save(WordEntry(f"word-{index}", [Sense("n.", "翻译"), Sense("v.", "另一条释义")],
                                [RelatedWord("child", [Sense("n.", "关联翻译")])],
                                learned=index % 3 == 0)) for index in range(count)]


def test_reveal_at_top_does_not_jump_to_selected_word_below(window, repo, qtbot):
    many_words(repo)
    window.set_filter("all")
    window.table.setCurrentIndex(window.model.index(70, 1))
    window.table.scrollToTop()
    qtbot.wait(20)
    index = window.model.index(0, 2)
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.table.visualRect(index).center())
    qtbot.wait(30)
    assert not window.model.rows[0].hidden
    assert window.table.verticalScrollBar().value() == 0


@pytest.mark.parametrize("action", ["important", "learned", "trash"])
def test_list_action_keeps_scroll(window, repo, qtbot, monkeypatch, action):
    many_words(repo)
    window.refresh()
    qtbot.wait(20)
    bar = window.table.verticalScrollBar()
    window.table.setCurrentIndex(window.model.index(40, 1))
    bar.setValue(310)
    entry_id = window.model.rows[12].entry.id
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: True)
    window.list_action(entry_id, action)
    qtbot.wait(30)
    assert bar.value() == 310


def test_filters_remember_independent_scroll_positions(window, repo, qtbot):
    many_words(repo)
    window.refresh()
    qtbot.wait(20)
    bar = window.table.verticalScrollBar()
    positions = {"pending": 350, "all": 600, "learned": 170}
    for key, position in positions.items():
        window.set_filter(key)
        qtbot.wait(20)
        assert bar.value() == 0
        bar.setValue(position)
    for key in ("pending", "all", "learned", "pending"):
        window.set_filter(key)
        qtbot.wait(20)
        assert bar.value() == positions[key]


def test_shuffle_button_scrolls_to_top_and_resets_only_pending_memory(window, repo, qtbot, monkeypatch):
    many_words(repo)
    monkeypatch.setattr("vocabulary.storage.random.shuffle", lambda entries: entries.reverse())
    window.set_filter("all")
    bar = window.table.verticalScrollBar()
    bar.setValue(600)
    window.set_filter("pending")
    window.table.setCurrentIndex(window.model.index(40, 1))
    bar.setValue(350)
    before = [row.entry.id for row in window.model.rows]
    qtbot.mouseClick(window.table.shuffle_button, Qt.MouseButton.LeftButton)
    qtbot.wait(30)
    assert [row.entry.id for row in window.model.rows] == before[::-1]
    assert bar.value() == 0 and window._filter_scroll["pending"] == 0
    window.set_filter("all")
    assert bar.value() == 600
    window.set_filter("pending")
    assert bar.value() == 0


def test_failed_shuffle_keeps_scroll_and_original_order(window, repo, qtbot, monkeypatch):
    many_words(repo)
    window.refresh()
    bar = window.table.verticalScrollBar()
    bar.setValue(350)
    before = list(window.model.rows)
    def fail():
        raise OSError("无法保存随机排序")
    monkeypatch.setattr(repo, "shuffle_pending", fail)
    qtbot.mouseClick(window.table.shuffle_button, Qt.MouseButton.LeftButton)
    qtbot.wait(30)
    assert bar.value() == 350 and window.model.rows == before
    assert window.toast.label.text() == "无法保存随机排序"


@pytest.fixture
def study_view(window, repo, monkeypatch, qtbot):
    context = StudyContext(date(2026, 11, 1), False)
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: context))
    for i in range(30):
        repo.save(WordEntry(f"study-{i}", [Sense("n.", "释义"), Sense("v.", "另一释义")],
                            [RelatedWord("child", [Sense("n.", "关联释义")])]))
    window.set_filter("study17")
    window.set_global_hidden(False)
    page = window.study_page
    qtbot.wait(20)
    assert page.table.verticalScrollBar().value() == 0
    assert not page.table.currentIndex().isValid()
    number = next(i for i, row in enumerate(page.model.rows)
                  if row.entry.id == page.tasks[18].entry.id and row.owner == 0)
    page.table.setCurrentIndex(page.model.index(number, 3))
    page.table.verticalScrollBar().setValue(347)
    return page


def selected_part(table):
    index = table.currentIndex()
    row = index.data(Qt.ItemDataRole.UserRole)
    return (row.entry.id, row.owner, row.sense, index.column()) if row else None


@pytest.mark.parametrize("other_page", ["pending", "all", "learned"])
def test_study_remembers_selection_and_scroll_on_return(study_view, window, qtbot, other_page):
    table = study_view.table
    expected = selected_part(table)
    for _ in range(2):
        window.set_filter(other_page)
        qtbot.wait(20)
        window.set_filter("study17")
        qtbot.wait(20)
        assert selected_part(table) == expected
        assert table.selectionModel().isSelected(table.currentIndex())
        assert table.verticalScrollBar().value() == 347


def test_study_refresh_and_visibility_preserve_view(study_view, window, qtbot):
    table = study_view.table
    expected = selected_part(table)
    qtbot.mouseClick(study_view.refresh_button, Qt.MouseButton.LeftButton)
    assert selected_part(table) == expected
    assert table.verticalScrollBar().value() == 347
    table.verticalScrollBar().setValue(100)
    window.set_global_hidden(True)
    assert selected_part(table) == (expected[0], -1, 0, 3)
    assert table.verticalScrollBar().value() == 100
    assert table.selectionModel().isSelected(table.currentIndex())


@pytest.mark.parametrize("clear_all", [False, True])
def test_study_missing_selected_word_clears_selection_and_clamps_scroll(study_view, window, repo, qtbot,
                                                                     clear_all):
    table = study_view.table
    table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
    position = table.verticalScrollBar().value()
    selected_id = selected_part(table)[0]
    window.set_filter("all")
    ids = [task.entry.id for task in study_view.tasks] if clear_all else [selected_id]
    for entry_id in ids:
        repo.delete(entry_id)
    window.set_filter("study17")
    qtbot.wait(20)
    assert not table.currentIndex().isValid()
    assert not table.selectionModel().hasSelection()
    assert table.verticalScrollBar().value() == min(position, table.verticalScrollBar().maximum())
    if clear_all:
        assert table.model().rowCount() == 0 and table.verticalScrollBar().value() == 0


def test_failed_study_refresh_keeps_selection_and_scroll(study_view, repo, monkeypatch):
    table = study_view.table
    expected = selected_part(table)

    def fail(*args):
        raise OSError("刷新失败")

    monkeypatch.setattr(repo, "prepare_study", fail)
    assert not study_view.reload()
    assert selected_part(table) == expected
    assert table.verticalScrollBar().value() == 347

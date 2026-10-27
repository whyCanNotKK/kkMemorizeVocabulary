import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry


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

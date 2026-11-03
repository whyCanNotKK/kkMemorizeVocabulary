"""Review interactions run through the real window in all four list pages."""
from datetime import date

import pytest
from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt
from PySide6.QtGui import QHelpEvent, QKeyEvent
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture(params=["pending", "all", "learned", "study17"])
def review_list(window, repo, qtbot, monkeypatch, request):
    page, count = request.param if isinstance(request.param, tuple) else (request.param, 3)
    context = StudyContext(date(2026, 11, 1), False)
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: context))
    names = ("alpha", "beta", "gamma") if count == 3 else [f"word-{i}" for i in range(count)]
    entries = [repo.save(WordEntry(
        name, [Sense("n.", "释义一"), Sense("v.", "释义二")],
        [RelatedWord(f"{name}-child", [Sense("n.", "关联一"), Sense("v.", "关联二")])],
        learned=page == "learned",
    )) for name in names]
    window.set_filter(page)
    table = window.study_page.table if page == "study17" else window.table
    qtbot.wait(20)
    return table, entries


def index_for(table, entry, *, owner=-1, sense=0, column=1):
    number = next(i for i, row in enumerate(table.model().rows)
                  if (row.entry.id, row.owner, row.sense) == (entry.id, owner, sense))
    return table.model().index(number, column)


def assert_selected(table, entry):
    row = table.currentIndex().data(Qt.ItemDataRole.UserRole)
    assert row.entry.id == entry.id
    assert row.first
    expected = {i for i, row in enumerate(table.model().rows) if row.entry.id == entry.id}
    assert {index.row() for index in table.selectionModel().selectedRows()} == expected


@pytest.mark.parametrize("button", [Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton])
def test_mask_click_reveals_and_selects_clicked_word(review_list, window, qtbot, button):
    table, entries = review_list
    table.setCurrentIndex(index_for(table, entries[-1]))
    window.filter_buttons[window.filter_name].setFocus()
    mask = table.visualRect(index_for(table, entries[0], column=2))
    QApplication.clipboard().setText("keep clipboard")
    qtbot.mouseClick(table.viewport(), button, pos=mask.topLeft() + QPoint(40, 18))
    assert table.delegate.editor is None and QApplication.activePopupWidget() is None
    assert QApplication.clipboard().text() == "keep clipboard"
    assert not table.model().rows[0].hidden
    assert_selected(table, entries[0])
    assert table.hasFocus()
    assert table.verticalScrollBar().value() == 0


@pytest.mark.parametrize("button", [Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton])
def test_mask_reveal_respects_invalid_inline_edit(review_list, qtbot, button):
    table, entries = review_list
    table.edit(index_for(table, entries[0]))
    editor = table.delegate.editor
    editor.setText("")
    mask = table.visualRect(index_for(table, entries[1], column=2))
    with qtbot.assertNotEmitted(table.revealRequested):
        qtbot.mouseClick(table.viewport(), button, pos=mask.topLeft() + QPoint(40, 18))
    assert table.delegate.editor is editor and editor.text() == ""
    assert all(row.hidden for row in table.model().rows if row.entry.id == entries[1].id)
    table.delegate.cancel()


@pytest.mark.parametrize("width", [860, 1300])
def test_mask_reveal_extends_vertically_but_not_horizontally(review_list, window, qtbot, width):
    table, entries = review_list
    window.resize(width, 820)
    qtbot.wait(20)
    cell = table.visualRect(index_for(table, entries[1], column=2))
    origin = cell.topLeft()
    mask = table.delegate.mask_rect(cell)
    for point in (
            origin + QPoint(5, 18),  # Left padding.
            QPoint(int(mask.left()) - 1, cell.top()),
            QPoint(int(mask.right()) + 1, cell.top() + 18),
            QPoint(cell.right() - 4, cell.top() + 18),  # Trailing translation space.
    ):
        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            with qtbot.assertNotEmitted(table.revealRequested):
                qtbot.mouseClick(table.viewport(), button, pos=point)
            assert all(row.hidden for row in table.model().rows)
            assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[1].id
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.MiddleButton, pos=origin + QPoint(40, 18))
    assert all(row.hidden for row in table.model().rows)
    for point in (origin + QPoint(40, 0), origin + QPoint(40, 5),
                  origin + QPoint(40, 30), origin + QPoint(12, 11),
                  QPoint(int(mask.right()) - 1, cell.bottom())):
        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            table.set_entry_revealed(entries[1].id, False)
            qtbot.mouseClick(table.viewport(), button, pos=point)
            assert_selected(table, entries[1])
            assert not table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden


def test_reveal_tooltip_only_applies_inside_mask(review_list, monkeypatch):
    table, entries = review_list
    tips = []
    monkeypatch.setattr("PySide6.QtWidgets.QToolTip.showText", lambda pos, text, *args: tips.append(text))
    origin = table.visualRect(index_for(table, entries[0], column=2)).topLeft()
    for offset in (QPoint(5, 18), QPoint(40, 5), QPoint(12, 11), QPoint(40, 30)):
        point = origin + offset
        event = QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point))
        QApplication.sendEvent(table.viewport(), event)
    assert tips == ["点击查看词性、翻译和关联词"] * 3


@pytest.mark.parametrize("down,up", [(Qt.Key.Key_S, Qt.Key.Key_W),
                                    (Qt.Key.Key_Down, Qt.Key.Key_Up)])
def test_navigation_skips_senses_and_related_words(review_list, qtbot, down, up):
    table, entries = review_list
    for entry in entries[:2]:
        table.revealRequested.emit(entry.id, True)
    table.setCurrentIndex(index_for(table, entries[0], owner=0, sense=1))
    table.setFocus()
    qtbot.keyClick(table, down)
    assert_selected(table, entries[1])
    qtbot.keyClick(table, down)
    assert_selected(table, entries[2])
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
    qtbot.keyClick(table, down)  # Stop at the last word.
    assert_selected(table, entries[2])
    qtbot.keyClick(table, up)
    assert_selected(table, entries[1])
    qtbot.keyClick(table, up)
    assert_selected(table, entries[0])
    qtbot.keyClick(table, up)  # Stop at the first word.
    assert_selected(table, entries[0])


def test_space_toggles_same_group_and_keeps_focus(review_list, qtbot):
    table, entries = review_list
    table.setCurrentIndex(index_for(table, entries[1]))
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert_selected(table, entries[1])
    assert not table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
    table.setCurrentIndex(index_for(table, entries[1], owner=0, sense=1, column=3))
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert_selected(table, entries[1])
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
    assert table.hasFocus()
    qtbot.keyClick(table, Qt.Key.Key_S)
    assert_selected(table, entries[2])


def test_review_keys_do_not_change_form_or_search_selection(window, repo, qtbot):
    entry = repo.save(WordEntry("alpha", [Sense("n.", "释义")]))
    window.refresh()
    window.table.setCurrentIndex(window.model.index(0, 1))
    for field in (window.blocks[0].word, window.search_box):
        field.setFocus()
        qtbot.keyClicks(field, "sw ")
        assert field.text() == "sw "
        assert entry.id not in window.revealed
    assert QApplication.focusWidget() is window.search_box


def test_space_repeat_and_missing_selection_do_not_toggle(review_list, qtbot):
    table, entries = review_list
    table.setFocus()
    table.setCurrentIndex(index_for(table, entries[0]))
    qtbot.keyClick(table, Qt.Key.Key_Space)
    for _ in range(3):
        repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                           Qt.KeyboardModifier.NoModifier, " ", True, 1)
        QApplication.sendEvent(table, repeat)
    assert_selected(table, entries[0])
    assert not table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
    # A current cell can exist with no actual selection.
    table.clearSelection()
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert not table.selectionModel().hasSelection()
    assert not table.model().rows[0].hidden
    table.setCurrentIndex(QModelIndex())
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert not table.currentIndex().isValid()


@pytest.mark.parametrize("column", [1, 2, 3])
def test_review_keys_belong_to_inline_editor_while_editing(window, repo, qtbot, column):
    entry = repo.save(WordEntry("alpha", [Sense("n.", "释义")],
                                [RelatedWord("child", [Sense("n.", "关联")])]))
    repo.save(WordEntry("beta", [Sense("n.", "第二个词")]))
    window.reveal(entry.id, True)
    table = window.table
    index = window.model.index(0, column)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    assert editor is not None
    editor.setFocus()
    qtbot.wait(10)
    qtbot.keyClicks(editor, "sw")
    qtbot.keyClick(editor, Qt.Key.Key_Down)
    qtbot.keyClick(editor, Qt.Key.Key_Up)
    qtbot.keyClick(editor, Qt.Key.Key_Space)
    assert table.currentIndex() == index
    assert entry.id in window.revealed
    if column != 2:
        assert "sw " in (editor.text() if column == 1 else editor.toPlainText())
    else:
        assert editor.view().isVisible()  # Space still opens the POS selector.
        editor.hidePopup()
    table.delegate.cancel()


def test_search_keeps_answers_revealed_but_allows_main_word_navigation(window, repo, qtbot):
    entries = [repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义")],
                                   [RelatedWord("child", [Sense("n.", "关联")])]))
               for i in range(2)]
    window.search_box.setText("word")
    table = window.table
    table.setCurrentIndex(window.model.index(0, 1))
    table.setFocus()
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert window.revealed == set()
    assert all(not row.hidden for row in window.model.rows)
    qtbot.keyClick(table, Qt.Key.Key_S)
    assert_selected(table, entries[1])


def test_modified_keys_leave_review_commands_alone(review_list, qtbot):
    table, entries = review_list
    table.setCurrentIndex(index_for(table, entries[1]))
    table.setFocus()
    for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.AltModifier,
                     Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.MetaModifier):
        for key in (Qt.Key.Key_W, Qt.Key.Key_S, Qt.Key.Key_Space):
            with qtbot.assertNotEmitted(table.revealRequested):
                qtbot.keyClick(table, key, modifier)
            assert table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[1].id


def test_empty_list_review_keys_are_noops(window, qtbot):
    table = window.table
    table.setFocus()
    for key in (Qt.Key.Key_W, Qt.Key.Key_S, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Space):
        qtbot.keyClick(table, key)
    assert table.model().rowCount() == 0
    assert not table.currentIndex().isValid()


def test_navigation_scrolls_to_main_word_and_space_keeps_position(window, repo, qtbot):
    entries = [repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义")],
                                   [RelatedWord("child", [Sense("n.", "关联")])]))
               for i in range(50)]
    window.set_global_hidden(False)
    table = window.table
    table.setCurrentIndex(index_for(table, entries[0]))
    table.setFocus()
    for _ in range(25):
        qtbot.keyClick(table, Qt.Key.Key_S)
    assert_selected(table, entries[25])
    rect = table.visualRect(table.currentIndex())
    assert table.viewport().rect().contains(rect)
    position = table.verticalScrollBar().value()
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert_selected(table, entries[25])
    assert table.verticalScrollBar().value() == position
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert_selected(table, entries[25])
    assert table.verticalScrollBar().value() == position


@pytest.mark.parametrize("review_list", ["study17"], indirect=True)
@pytest.mark.parametrize("key,modifier", [
    (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier),
])
def test_study_enter_reveals_then_completes_parent_word(review_list, window, repo, qtbot, key, modifier):
    table, entries = review_list
    entry = entries[1]
    table.setCurrentIndex(index_for(table, entry))
    table.setFocus()
    qtbot.keyClick(table, key, modifier)
    assert_selected(table, entry)
    assert not table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
    assert entry.id in {task.entry.id for task in repo.study_tasks(window.study_page.context)}
    for _ in range(3):
        repeat = QKeyEvent(QEvent.Type.KeyPress, key, modifier, "\r", True, 1)
        QApplication.sendEvent(table, repeat)
    assert_selected(table, entry)  # Holding Enter cannot complete the newly revealed word.
    table.setCurrentIndex(index_for(table, entry, owner=0, sense=1, column=3))
    with qtbot.waitSignal(table.actionRequested) as signal:
        qtbot.keyClick(table, key, modifier)
    assert signal.args == [entry.id, "study"]
    assert entry.id not in {row.entry.id for row in table.model().rows}
    assert entry.id not in {task.entry.id for task in repo.study_tasks(window.study_page.context)}
    assert not repo.get(entry.id).learned
    assert table.hasFocus()


@pytest.mark.parametrize("review_list", ["pending", "all", "learned"], indirect=True)
def test_enter_does_not_perform_study_actions_on_other_pages(review_list, qtbot):
    table, entries = review_list
    for revealed in (False, True):
        table.revealRequested.emit(entries[0].id, revealed)
        table.setCurrentIndex(index_for(table, entries[0]))
        table.setFocus()
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            with qtbot.assertNotEmitted(table.actionRequested), qtbot.assertNotEmitted(table.revealRequested):
                qtbot.keyClick(table, key)
            assert table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden is not revealed


@pytest.mark.parametrize("review_list", ["study17"], indirect=True)
def test_study_enter_requires_selection_and_unmodified_press(review_list, window, qtbot):
    table, entries = review_list
    table.setCurrentIndex(index_for(table, entries[0]))
    table.setFocus()
    for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.AltModifier,
                     Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.MetaModifier):
        with qtbot.assertNotEmitted(table.revealRequested), qtbot.assertNotEmitted(table.actionRequested):
            qtbot.keyClick(table, Qt.Key.Key_Return, modifier)
    assert table.model().rows[0].hidden
    table.clearSelection()
    with qtbot.assertNotEmitted(table.revealRequested), qtbot.assertNotEmitted(table.actionRequested):
        qtbot.keyClick(table, Qt.Key.Key_Return)
    window.study_page.refresh_button.setFocus()
    with qtbot.assertNotEmitted(table.actionRequested):
        qtbot.keyClick(window.study_page.refresh_button, Qt.Key.Key_Return)


@pytest.mark.parametrize("review_list", ["study17"], indirect=True)
def test_study_enter_completion_failure_keeps_word_selected(review_list, window, repo, qtbot, monkeypatch):
    table, entries = review_list
    table.revealRequested.emit(entries[0].id, True)
    table.setCurrentIndex(index_for(table, entries[0]))
    table.setFocus()

    def fail(*args):
        raise OSError("无法保存学习进度")

    monkeypatch.setattr(repo, "complete_study", fail)
    qtbot.keyClick(table, Qt.Key.Key_Return)
    assert_selected(table, entries[0])
    assert not table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden
    assert window.toast.label.text() == "无法保存学习进度"


@pytest.mark.parametrize("review_list", ["study17"], indirect=True)
def test_study_button_tooltip_mentions_enter(review_list, monkeypatch):
    table, entries = review_list
    tips = []
    monkeypatch.setattr("PySide6.QtWidgets.QToolTip.showText", lambda pos, text, *args: tips.append(text))
    rect = table.visualRect(index_for(table, entries[0], column=4))
    point = table.action_rects(rect)[0][1].center()
    event = QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point))
    QApplication.sendEvent(table.viewport(), event)
    assert tips == ["记下（回车）"]


def test_ctrl_arrows_scroll_without_changing_current_cell_or_group(review_list, window, repo, qtbot):
    table, entries = review_list
    entries[0].senses[0].translation = "\n".join(["多行释义"] * 60)
    repo.save(entries[0])
    if table.study_mode:
        window.study_page.reload()
    else:
        window.refresh()
    for entry in entries[:2]:
        table.revealRequested.emit(entry.id, True)
    current = index_for(table, entries[1], owner=0, sense=1, column=3)
    table.setCurrentIndex(current)
    table.setFocus()
    bar = table.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    selected = table.selectionModel().selectedIndexes()
    hidden = [row.hidden for row in table.model().rows]

    def unchanged():
        assert table.currentIndex() == current
        assert table.selectionModel().selectedIndexes() == selected
        assert [row.hidden for row in table.model().rows] == hidden

    qtbot.keyClick(table, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
    assert bar.value() > 0
    unchanged()
    previous = bar.value()
    for _ in range(3):
        repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down,
                           Qt.KeyboardModifier.ControlModifier, "", True, 1)
        QApplication.sendEvent(table, repeat)
    assert bar.value() > previous
    unchanged()
    previous = bar.value()
    qtbot.keyClick(table, Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)
    assert bar.value() < previous
    unchanged()
    # Reaching either edge must not scroll back to the selected word.
    for boundary, key in ((0, Qt.Key.Key_Up), (bar.maximum(), Qt.Key.Key_Down)):
        bar.setValue(boundary)
        qtbot.keyClick(table, key, Qt.KeyboardModifier.ControlModifier)
        assert bar.value() == boundary
        unchanged()
    table.clearSelection()
    bar.setValue(0)
    qtbot.keyClick(table, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
    assert bar.value() > 0
    assert not table.selectionModel().hasSelection()
    assert table.currentIndex() == current


def test_ctrl_arrows_with_no_scroll_range_do_not_select_word(review_list, qtbot):
    table, _ = review_list
    table.setCurrentIndex(QModelIndex())
    table.setFocus()
    assert table.verticalScrollBar().maximum() == 0
    for key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
        qtbot.keyClick(table, key, Qt.KeyboardModifier.ControlModifier)
        assert table.verticalScrollBar().value() == 0
        assert not table.currentIndex().isValid()
        assert not table.selectionModel().hasSelection()


def test_ctrl_arrows_in_form_and_search_do_not_scroll_list(window, repo, qtbot):
    for i in range(40):
        repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义")]))
    window.search_box.setText("word")
    table = window.table
    table.setCurrentIndex(window.model.index(5, 1))
    bar = table.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    current, position = table.currentIndex(), bar.value()
    for field in (window.blocks[0].word, window.blocks[0].rows[0].translation, window.search_box):
        field.setFocus()
        for key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            qtbot.keyClick(field, key, Qt.KeyboardModifier.ControlModifier)
            assert bar.value() == position
            assert table.currentIndex() == current


@pytest.mark.parametrize("review_list", ["study17"], indirect=True)
@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("trigger", ["mouse", "return", "keypad"])
@pytest.mark.parametrize("target_revealed", [False, True])
def test_study_completion_selects_same_task_position(review_list, window, qtbot, position, trigger,
                                                    target_revealed):
    table, entries = review_list
    entry = entries[position]
    remaining = [item for item in entries if item.id != entry.id]
    target = remaining[min(position, len(remaining) - 1)]
    if target_revealed:
        table.revealRequested.emit(target.id, True)
    table.revealRequested.emit(entry.id, True)
    # A related sense's physical row number must not be used as the task number.
    table.setCurrentIndex(index_for(table, entry, owner=0, sense=1, column=3))
    if trigger == "mouse":
        window.study_page.refresh_button.setFocus()
        rect = table.visualRect(index_for(table, entry, column=4))
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                        pos=table.action_rects(rect)[0][1].center())
    else:
        table.setFocus()
        key = Qt.Key.Key_Return if trigger == "return" else Qt.Key.Key_Enter
        modifier = (Qt.KeyboardModifier.NoModifier if trigger == "return"
                    else Qt.KeyboardModifier.KeypadModifier)
        qtbot.keyClick(table, key, modifier)
        repeat = QKeyEvent(QEvent.Type.KeyPress, key, modifier, "\r", True, 1)
        QApplication.sendEvent(table, repeat)
    assert [task.entry.id for task in window.study_page.tasks] == [item.id for item in remaining]
    assert_selected(table, target)
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).hidden is not target_revealed
    assert table.hasFocus()


@pytest.mark.parametrize("review_list", ["study17"], indirect=True)
@pytest.mark.parametrize("trigger", ["mouse", "return", "keypad"])
def test_completing_final_study_word_leaves_clean_empty_list(review_list, window, qtbot, trigger):
    table, entries = review_list
    page = window.study_page
    for entry in entries[:-1]:
        page.complete(entry.id, "study")
    entry = entries[-1]
    table.revealRequested.emit(entry.id, True)
    table.setCurrentIndex(index_for(table, entry))
    table.setFocus()
    if trigger == "mouse":
        rect = table.visualRect(index_for(table, entry, column=4))
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                        pos=table.action_rects(rect)[0][1].center())
    else:
        key = Qt.Key.Key_Return if trigger == "return" else Qt.Key.Key_Enter
        modifier = (Qt.KeyboardModifier.NoModifier if trigger == "return"
                    else Qt.KeyboardModifier.KeypadModifier)
        qtbot.keyClick(table, key, modifier)
    assert page.tasks == [] and table.model().rowCount() == 0
    assert not table.currentIndex().isValid()
    assert not table.selectionModel().hasSelection()
    assert table.hasFocus()
    assert page.task_count.accessibleName() == f"当前单词任务：0/{len(entries)}"
    assert "没有待完成的单词" in table.empty_text
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space, Qt.Key.Key_S):
        qtbot.keyClick(table, key)
    assert not table.currentIndex().isValid()


@pytest.mark.parametrize("review_list", [(page, 30) for page in ("pending", "all", "learned", "study17")],
                         indirect=True)
@pytest.mark.parametrize("layout", ["hidden", "expanded", "related_at_top", "only_related_visible"])
def test_navigation_without_selection_uses_visible_main_word(review_list, window, repo, qtbot, layout):
    table, entries = review_list
    # Study tasks may have a different within-group order; use the displayed order.
    entries = [row.entry for row in table.model().rows if row.first]
    if layout == "only_related_visible":
        entries[7].related[0].senses[0].translation = "\n".join(["关联词释义"] * 70)
        repo.save(entries[7])
        if table.study_mode:
            window.study_page.reload()
        else:
            window.refresh()
    if layout != "hidden":
        if table.study_mode:
            window.study_page.set_global_hidden(False)
        else:
            window.set_global_hidden(False)
    qtbot.wait(20)
    if layout in ("related_at_top", "only_related_visible"):
        top = index_for(table, entries[7], owner=0).row()
        expected = entries[8] if layout == "related_at_top" else entries[7]
    else:
        top = index_for(table, entries[7]).row()
        expected = entries[7]
    position = table.rowViewportPosition(top) + table.verticalScrollBar().value() + 3
    hidden = [row.hidden for row in table.model().rows]
    table.setFocus()
    for current_entry in (None, entries[-1], expected):
        for key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_W, Qt.Key.Key_S):
            if current_entry is not None:
                table.setCurrentIndex(index_for(table, current_entry))
                table.clearSelection()
            else:
                table.setCurrentIndex(QModelIndex())
            table.verticalScrollBar().setValue(position)
            assert table.verticalScrollBar().value() == position
            assert not table.selectionModel().hasSelection()
            qtbot.keyClick(table, key)
            assert_selected(table, expected)
            assert table.verticalScrollBar().value() == position
            assert [row.hidden for row in table.model().rows] == hidden

from datetime import date

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QHelpEvent, QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication

from vocabulary.domain import POS_OPTIONS, RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.study_input import STUDY_TRANSLATION_HEADER
from vocabulary.theme import GOLD, TEXT


@pytest.fixture
def practice(window, repo, monkeypatch):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 2), False)))
    entry = repo.save(WordEntry("Ab  c-d's", [Sense("n.", "名词"), Sense("adj.", "形容词")],
                               [RelatedWord("child", [Sense("v.", "关联词")])]))
    repo.save(WordEntry("next", [Sense("v.", "下一个")]))
    window.set_filter("study17")
    table = window.study_page.table
    table.select_entry(entry.id)
    table.setFocus()
    return table, entry


def row_for(table, entry):
    return next(row for row in table.model().rows if row.entry.id == entry.id)


@pytest.mark.parametrize("code,keys", [
    ("n.", "1nN"), ("v.", "2vV"), ("adj.", "3aA"), ("adv.", "4"),
    ("prep.", "5pP"), ("pron.", "6"), ("conj.", "7cC"),
    ("interj.", "8iI"), ("else", "9eE"),
])
def test_pos_keys_reveal_only_matching_main_senses(practice, window, repo, qtbot, code, keys):
    table, entry = practice
    entry.senses = [Sense(code, "释义")]
    repo.save(entry)
    window.study_page.reload()
    table.select_entry(entry.id)
    for character in keys:
        qtbot.keyClicks(table, character)
        assert not row_for(table, entry).hidden
        assert table.study_input.position == 0
        table.set_entry_revealed(entry.id, False)
    qtbot.keyClick(table, Qt.Key.Key_1 + next(i for i, pos in enumerate(POS_OPTIONS) if pos[0] == code),
                   Qt.KeyboardModifier.KeypadModifier)
    assert not row_for(table, entry).hidden


def test_hidden_keys_and_mouse_mark_cannot_complete(practice, window, repo, qtbot):
    table, entry = practice
    before = repo.study_backup()
    for key in (Qt.Key.Key_Space, Qt.Key.Key_V,
                Qt.Key.Key_2, Qt.Key.Key_Left, Qt.Key.Key_Right):
        qtbot.keyClick(table, key)
    index = table.currentIndex().siblingAtColumn(table.action_column)
    rect = table.visualRect(index)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                    pos=dict(table.action_rects(rect, row_for(table, entry)))["study"].center())
    assert row_for(table, entry).hidden
    assert repo.study_backup() == before
    assert table.study_input.position == 0


def test_spelling_is_strict_and_completes_from_related_row(practice, window, repo, qtbot):
    table, entry = practice
    qtbot.keyClicks(table, "a")  # The second main sense also reveals; not spelling input.
    assert not row_for(table, entry).hidden
    assert table.study_input.position == 0
    qtbot.keyClicks(table, "a")  # Wrong case.
    assert table.study_input.position == 0
    qtbot.keyClicks(table, "Ab ")
    assert table.study_input.position == 3
    qtbot.keyClicks(table, "c")  # A second space is required.
    assert table.study_input.position == 0
    qtbot.keyClicks(table, "Ab")
    related = next(i for i, row in enumerate(table.model().rows)
                   if row.entry.id == entry.id and row.owner == 0)
    table.setCurrentIndex(table.model().index(related, 3))
    assert table.study_input.position == 2
    qtbot.keyClicks(table, entry.text[2:])
    assert entry.id not in {task.entry.id for task in window.study_page.tasks}
    assert table.study_input.position == 0
    assert not repo.get(entry.id).learned
    assert table.hasFocus()


def test_space_error_is_consumed_and_next_space_hides(practice, qtbot):
    table, entry = practice
    qtbot.keyClicks(table, "nA ")
    assert not row_for(table, entry).hidden
    assert table.study_input.position == 0
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert row_for(table, entry).hidden
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert row_for(table, entry).hidden


@pytest.mark.parametrize("reset_key", [Qt.Key.Key_Backspace, Qt.Key.Key_Escape])
def test_reset_keys_clear_progress(practice, qtbot, reset_key):
    table, entry = practice
    qtbot.keyClicks(table, "nAb")
    assert table.study_input.position == 2
    qtbot.keyClick(table, reset_key)
    assert table.study_input.position == 0
    assert not row_for(table, entry).hidden


def test_definition_hover_within_group_keeps_spelling_progress(practice, qtbot):
    table, entry = practice
    qtbot.keyClicks(table, 'nAb')
    assert table.study_input.position == 2
    target = table._entry_index(entry.id, owner=0, column=3)
    qtbot.mouseMove(table.viewport(), table.viewport().rect().topLeft())
    qtbot.mouseMove(table.viewport(), table.visualRect(target).center())
    assert table.currentIndex() == target
    assert table.study_input.entry_id == entry.id and table.study_input.position == 2
    assert table.hasFocus()


@pytest.mark.parametrize("transition", ["word", "selection", "focus", "page", "refresh",
                                        "visibility", "edit", "drag"])
def test_progress_resets_on_context_change(practice, window, qtbot, transition):
    table, entry = practice
    qtbot.keyClicks(table, "nAb")
    assert table.study_input.position == 2
    if transition == "word":
        qtbot.keyClick(table, Qt.Key.Key_Down)
    elif transition == "selection":
        table.clearSelection()
    elif transition == "focus":
        window.study_page.refresh_button.setFocus()
    elif transition == "page":
        window.set_filter("pending")
    elif transition == "refresh":
        window.study_page.reload()
    elif transition == "visibility":
        window.study_page.set_global_hidden(False)
    elif transition == "edit":
        qtbot.keyClick(table, Qt.Key.Key_F2)
        assert table.delegate.editor is not None
        table.delegate.cancel()
    else:
        # Use the actual sense drag handle and cross the system drag threshold.
        index = table.currentIndex().siblingAtColumn(2)
        point = table.delegate.pos_rect(index, table.visualRect(index)).center().toPoint()
        qtbot.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        qtbot.mouseMove(table.viewport(), pos=point + table.viewport().rect().bottomRight() / 3)
        qtbot.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert table.study_input.position == 0


def test_paste_ime_repeat_and_modified_keys_do_not_feed_spelling(practice, qtbot):
    table, entry = practice
    QApplication.sendEvent(table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_N,
                                           Qt.KeyboardModifier.NoModifier, "n", True))
    assert row_for(table, entry).hidden
    qtbot.keyClicks(table, "nA")
    QApplication.clipboard().setText(entry.text)
    qtbot.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    event = QInputMethodEvent()
    event.setCommitString(entry.text[1:])
    QApplication.sendEvent(table, event)
    QApplication.sendEvent(table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B,
                                           Qt.KeyboardModifier.NoModifier, "b", True))
    for modifier in (Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.MetaModifier):
        qtbot.keyClick(table, Qt.Key.Key_B, modifier)
    assert table.study_input.position == 1
    assert not row_for(table, entry).hidden


def test_repeated_key_cannot_start_spelling_next_visible_word(practice, window, qtbot):
    table, entry = practice
    window.study_page.set_global_hidden(False)
    qtbot.keyClicks(table, entry.text)
    assert table.currentIndex().data() == "next"
    QApplication.sendEvent(table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_N,
                                           Qt.KeyboardModifier.NoModifier, "n", True))
    assert table.study_input.position == 0


def test_no_selection_or_focus_prevents_practice(practice, window, qtbot):
    table, entry = practice
    table.clearSelection()
    qtbot.keyClicks(table, "n")
    assert row_for(table, entry).hidden
    table.select_entry(entry.id)
    window.study_page.refresh_button.setFocus()
    qtbot.keyClicks(table, "n")
    assert row_for(table, entry).hidden


def test_failed_refresh_clears_progress_but_keeps_visible_word(practice, window, qtbot, monkeypatch):
    table, entry = practice
    qtbot.keyClicks(table, "nAb")
    def fail(*args):
        raise OSError("refresh failed")
    monkeypatch.setattr(window.repository, "prepare_study", fail)
    assert not window.study_page.reload()
    assert table.study_input.position == 0
    assert not row_for(table, entry).hidden


def test_header_is_single_line_and_only_truncated_title_has_tooltip(practice, monkeypatch, qtbot):
    table, _ = practice
    header = table.horizontalHeader()
    before = header.height()
    assert table.model().headerData(3, Qt.Orientation.Horizontal) == STUDY_TRANSLATION_HEADER
    assert table.model().headerData(3, Qt.Orientation.Horizontal,
                                    Qt.ItemDataRole.ToolTipRole) == STUDY_TRANSLATION_HEADER
    table.resize_boundary(3, 200)
    assert header.height() == before
    tips = []
    monkeypatch.setattr('PySide6.QtWidgets.QToolTip.showText', lambda pos, text, *args: tips.append(text))
    table.window().resize(860, 820)
    qtbot.wait(20)
    def hover():
        point = header.viewport().rect().topLeft()
        point.setX(header.sectionViewportPosition(3) + 12)
        point.setY(header.height() // 2)
        QApplication.sendEvent(header.viewport(), QHelpEvent(
            QEvent.Type.ToolTip, point, header.viewport().mapToGlobal(point)))
    hover()
    assert tips == [STUDY_TRANSLATION_HEADER]
    table.window().resize(2000, 820)
    qtbot.wait(20)
    hover()
    assert tips == [STUDY_TRANSLATION_HEADER]
    table.window().resize(860, 820)
    qtbot.wait(20)
    hover()
    assert tips == [STUDY_TRANSLATION_HEADER] * 2


def test_colored_prefix_is_transient_and_does_not_change_model_text(practice, qtbot):
    table, entry = practice
    qtbot.keyClicks(table, "n")
    rect = table.visualRect(table.currentIndex())
    before = table.viewport().grab(rect).toImage()
    qtbot.keyClicks(table, "Ab")
    colored = table.viewport().grab(rect).toImage()
    assert colored != before
    assert any(colored.pixelColor(x, y) == QColor(GOLD)
               for x in range(colored.width()) for y in range(colored.height()))
    assert table.currentIndex().data() == entry.text
    qtbot.keyClick(table, Qt.Key.Key_Escape)
    restored = table.viewport().grab(rect).toImage()
    assert restored == before


@pytest.mark.parametrize('hide', [False, True])
@pytest.mark.parametrize('reset', ['error', 'escape', 'backspace', 'hide', 'focus'])
def test_spelling_visibility_only_changes_painted_content(practice, window, qtbot, hide, reset):
    table, entry = practice
    window.repository.set_study_hide_word_while_spelling(hide)
    window.study_page.render()
    qtbot.keyClicks(table, 'n')
    rect = table.visualRect(table.currentIndex())
    original = table.viewport().grab(rect).toImage()
    qtbot.keyClicks(table, 'Ab')
    assert table.delegate.editor is None
    assert table.currentIndex().data() == entry.text
    painted = table.viewport().grab(rect).toImage()
    has_white = any(painted.pixelColor(x, y) == QColor(TEXT)
                    for x in range(painted.width()) for y in range(painted.height()))
    assert has_white is not hide
    assert any(painted.pixelColor(x, y) == QColor(GOLD)
               for x in range(painted.width()) for y in range(painted.height()))
    if reset == 'error':
        qtbot.keyClicks(table, 'x')
    elif reset == 'escape':
        qtbot.keyClick(table, Qt.Key.Key_Escape)
    elif reset == 'backspace':
        qtbot.keyClick(table, Qt.Key.Key_Backspace)
    elif reset == 'hide':
        table.set_entry_revealed(entry.id, False)
        table.set_entry_revealed(entry.id, True)
    else:
        window.study_page.refresh_button.setFocus()
        table.setFocus()
    assert table.study_input.position == 0
    assert table.viewport().grab(rect).toImage() == original
    assert table.delegate.editor is None


def test_hidden_spelling_suppresses_original_word_tooltip(practice, window, qtbot):
    table, entry = practice
    table.setColumnWidth(1, table.MIN_WORD_WIDTH)
    window.repository.set_study_hide_word_while_spelling(True)
    window.study_page.render()
    qtbot.keyClicks(table, 'n')
    assert entry.text in table.delegate.word_tooltip(table.currentIndex())
    qtbot.keyClicks(table, 'Ab')
    assert table.delegate.word_tooltip(table.currentIndex()) == ''
    qtbot.keyClick(table, Qt.Key.Key_Escape)
    assert entry.text in table.delegate.word_tooltip(table.currentIndex())

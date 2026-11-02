from datetime import date

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture(params=['pending', 'study17'])
def inline_editor(request, window, repo, qtbot, monkeypatch):
    entry = repo.save(WordEntry('word', [Sense('n.', 'original')],
                               [RelatedWord('child', [Sense('v.', 'related')])]))
    if request.param == 'study17':
        monkeypatch.setattr(StudyContext, 'now', classmethod(
            lambda cls: StudyContext(date(2026, 11, 2), False)))
        window.set_filter('study17')
        window.study_page.reveal(entry.id, True)
    else:
        window.refresh()
        window.reveal(entry.id, True)
    table = window.current_table()
    table.edit(table.model().index(0, 3))
    editor = table.delegate.editor
    qtbot.wait(20)
    yield table, editor, entry
    table.delegate.cancel()


def test_inline_newlines_grow_row_and_deleting_shrinks_without_saving(inline_editor, repo, qtbot):
    table, editor, entry = inline_editor
    initial = editor.height()
    editor.moveCursor(editor.textCursor().MoveOperation.End)
    for _ in range(3):
        qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        qtbot.keyClicks(editor, 'another line')
    qtbot.waitUntil(lambda: editor.height() > initial)
    qtbot.waitUntil(lambda: editor.verticalScrollBar().maximum() == 0)
    assert table.rowHeight(0) >= editor.height()
    assert table.viewport().rect().contains(editor.geometry())
    assert table.delegate.editor is editor
    assert repo.get(entry.id).senses[0].translation == 'original'
    editor.setPlainText('short')
    qtbot.waitUntil(lambda: editor.height() == 36)
    assert table.rowHeight(0) >= editor.height()


def test_inline_wrap_reflows_on_window_width_change(inline_editor, window, repo, qtbot):
    table, editor, entry = inline_editor
    editor.setPlainText('a long definition ' * 30)
    qtbot.waitUntil(lambda: editor.height() > 36)
    qtbot.wait(20)
    wide = editor.height()
    window.resize(860, 820)
    qtbot.waitUntil(lambda: editor.height() > wide)
    narrow = editor.height()
    window.resize(1300, 820)
    qtbot.waitUntil(lambda: editor.height() < narrow)
    qtbot.waitUntil(lambda: editor.verticalScrollBar().maximum() == 0)
    assert table.delegate.editor is editor
    assert repo.get(entry.id).senses[0].translation == 'original'


def test_inline_very_long_text_stays_in_viewport_then_shrinks(inline_editor, qtbot):
    table, editor, _ = inline_editor
    editor.setPlainText('\n'.join(['long content'] * 100))
    qtbot.waitUntil(lambda: editor.height() > 100)
    qtbot.waitUntil(lambda: editor.verticalScrollBar().maximum() > 0)
    assert editor.height() <= table.viewport().height()
    assert table.viewport().rect().contains(editor.geometry())
    editor.setPlainText('short')
    qtbot.waitUntil(lambda: editor.height() == 36)
    qtbot.waitUntil(lambda: editor.verticalScrollBar().maximum() == 0)


@pytest.mark.parametrize('save', [False, True])
def test_inline_finish_recalculates_saved_row_height(inline_editor, repo, qtbot, save):
    table, editor, entry = inline_editor
    editor.setPlainText('first\nsecond\nthird\nfourth')
    qtbot.waitUntil(lambda: editor.height() > 60)
    expanded = table.rowHeight(0)
    qtbot.keyClick(editor, Qt.Key.Key_Return if save else Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: table.delegate.editor is None)
    if save:
        qtbot.waitUntil(lambda: table.model().index(0, 3).data() == 'first\nsecond\nthird\nfourth')
        assert repo.get(entry.id).senses[0].translation == 'first\nsecond\nthird\nfourth'
        assert table.rowHeight(0) > 60
    else:
        qtbot.waitUntil(lambda: table.rowHeight(0) < expanded)
        assert repo.get(entry.id).senses[0].translation == 'original'


@pytest.mark.parametrize('related', [False, True])
@pytest.mark.parametrize('sense_index', [0, 1])
def test_form_translation_and_panel_grow_and_shrink(window, qtbot, related, sense_index):
    if related:
        window.add_related()
    block = window.blocks[-1]
    if sense_index:
        block.add_sense()
    row = block.rows[sense_index]
    editor = row.translation
    qtbot.wait(30)
    initial_panel = window.editor_panel.height()
    editor.setPlainText('first\nsecond\nthird')
    qtbot.waitUntil(lambda: editor.height() > 36)
    qtbot.waitUntil(lambda: window.editor_panel.height() > initial_panel)
    assert row.height() == editor.height()
    assert editor.verticalScrollBar().maximum() == 0
    assert row.pos.height() == 36 and block.word.height() == 36
    editor.clear()
    qtbot.waitUntil(lambda: editor.height() == 36)
    qtbot.waitUntil(lambda: window.editor_panel.height() == initial_panel)


def test_form_wrapped_text_reflows_on_resize(window, qtbot):
    editor = window.blocks[0].rows[0].translation
    editor.setPlainText('a long definition ' * 30)
    qtbot.waitUntil(lambda: editor.height() > 36)
    qtbot.wait(30)
    wide = editor.height()
    window.resize(860, 820)
    qtbot.waitUntil(lambda: editor.height() > wide)
    narrow = editor.height()
    window.resize(1300, 820)
    qtbot.waitUntil(lambda: editor.height() < narrow)
    assert editor.verticalScrollBar().maximum() == 0
    assert editor.toPlainText() == 'a long definition ' * 30


def test_form_uses_outer_scroll_at_limit_and_recovers_on_clear(window, qtbot):
    editor = window.blocks[0].rows[0].translation
    initial = window.editor_panel.height()
    editor.setPlainText('\n'.join(['content'] * 100))
    qtbot.waitUntil(lambda: window.editor_panel.height() == window.centralWidget().height() // 2)
    qtbot.waitUntil(lambda: window.form_scroll.verticalScrollBar().maximum() > 0)
    assert editor.height() > window.editor_panel.height()
    assert editor.verticalScrollBar().maximum() == 0
    editor.clear()
    qtbot.waitUntil(lambda: editor.height() == 36)
    qtbot.waitUntil(lambda: window.editor_panel.height() == initial)
    qtbot.waitUntil(lambda: window.form_scroll.verticalScrollBar().maximum() == 0)


def test_opening_existing_multiline_form_fits_content(window, qtbot):
    window.reset_form(WordEntry('loaded', [Sense('n.', 'first\nsecond\nthird')]))
    editor = window.blocks[0].rows[0].translation
    qtbot.waitUntil(lambda: editor.height() > 36)
    assert editor.verticalScrollBar().maximum() == 0
    assert editor.toPlainText() == 'first\nsecond\nthird'


def test_tall_form_editor_keeps_cursor_visible_when_moving_between_ends(window, qtbot):
    editor = window.blocks[0].rows[0].translation
    editor.setFocus()
    editor.setPlainText('\n'.join(['content'] * 100))
    qtbot.waitUntil(lambda: editor.height() > window.editor_panel.height())

    def cursor_visible():
        point = editor.viewport().mapTo(window.form_scroll.viewport(), editor.cursorRect().center())
        return window.form_scroll.viewport().rect().contains(point)

    qtbot.keyClick(editor, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(cursor_visible)
    assert window.form_scroll.verticalScrollBar().value() > 0
    qtbot.keyClick(editor, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(cursor_visible)
    assert window.form_scroll.verticalScrollBar().value() == 0

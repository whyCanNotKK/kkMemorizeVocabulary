from datetime import date
import json

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent, QKeyEvent, QTextDocument
from PySide6.QtWidgets import QApplication, QToolTip

from vocabulary.domain import DEFAULT_SHORTCUTS, RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.shortcuts import RESERVED_DIGITS, SettingsDialog
from vocabulary.storage import Repository
from vocabulary.theme import copy_word_tooltip
from vocabulary.word_clipboard import FORMAT, decode, encode


CTRL = Qt.KeyboardModifier.ControlModifier
LEFT = Qt.MouseButton.LeftButton
RIGHT = Qt.MouseButton.RightButton
REPLACEMENT = RelatedWord("新词", [Sense("v.", "第一行\n第二行"), Sense("n.", ""), Sense("else", "其他")], "/ʌ/")


def clip(text=None):
    clipboard = QApplication.clipboard()
    if text is not None:
        clipboard.setText(text)
    return clipboard.text()


def numeric(qtbot, number, modifier=CTRL):
    qtbot.keyClick(QApplication.focusWidget(), getattr(Qt.Key, f"Key_{number}"), modifier)


def paste(qtbot, field, word=REPLACEMENT):
    field.setFocus()
    clip(encode(word))
    qtbot.keyClick(field, Qt.Key.Key_V, CTRL)


def entry_with_children(repo, *, learned=False):
    return repo.save(WordEntry("main", [Sense("n.", "main"), Sense("v.", "more")],
                               [RelatedWord(name, [Sense("n.", name), Sense("v.", "")])
                                for name in ("first", "second")], learned=learned))


def action_point(table, index, action):
    return dict(table.action_rects(table.visualRect(index), index.data(Qt.ItemDataRole.UserRole)))[action].center()


def test_word_clipboard_protocol():
    payload = encode(RelatedWord(" 新词 ", [Sense("v.", "第一行\n第二行"), Sense("n.", ""),
                                       Sense("其他", "其他"), Sense()], " /ʌ/ "))
    assert decode(payload) == REPLACEMENT
    document = json.loads(payload)
    assert set(document) == {"format", "version", "text", "phonetic", "senses"}
    assert document["format"] == FORMAT and document["version"] == 1
    assert decode('{"format":"kkMemorizeVocabulary","words":[]}') is None
    assert decode("ordinary text") is None
    for changes in ({"version": True}, {"version": 2}, {"related": []}, {"text": " "},
                    {"phonetic": None}, {"senses": []}, {"senses": [{"pos": "x", "translation": ""}]}):
        with pytest.raises(ValidationError):
            decode(json.dumps(document | changes))


@pytest.mark.parametrize("page", ["pending", "all", "learned", "study17"])
def test_list_copy_hidden_then_related_right_click(window, repo, qtbot, monkeypatch, page):
    entry = entry_with_children(repo, learned=page == "learned")
    context = StudyContext(date(2026, 11, 6), False)
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: context))
    window.set_filter(page)
    window.refresh()
    table = window.current_table()
    table.select_entry(entry.id)
    table.setFocus()
    action_index = table.model().index(0, table.action_column)
    assert table.model().rows[0].hidden
    expected_actions = {
        "pending": ["drag", "copy", "edit", "learned", "trash"],
        "all": ["copy", "edit", "learned", "trash"],
        "learned": ["drag", "copy", "edit", "learned", "trash"],
        "study17": ["copy", "study"],
    }
    assert list(dict(table.action_rects(table.visualRect(action_index)))) == expected_actions[page]
    if page != "study17":
        mark = '标记为"待记"' if page == "learned" else '标记为"已记下"'
        for action, tooltip in [("edit", "编辑单词"), ("learned", mark)]:
            point = action_point(table, action_index, action)
            event = QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point))
            QApplication.sendEvent(table.viewport(), event)
            assert QToolTip.text() == tooltip
    point = action_point(table, action_index, "copy")
    help_event = QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point))
    QApplication.sendEvent(table.viewport(), help_event)
    assert QToolTip.text() == copy_word_tooltip()
    document = QTextDocument()
    document.setDefaultFont(table.font())
    document.setHtml(QToolTip.text())
    document.adjustSize()
    assert document.begin().layout().lineCount() == 1
    qtbot.mouseClick(table.viewport(), LEFT, pos=point)
    assert decode(clip()) == RelatedWord(entry.text, entry.senses, "")
    assert window.toast.label.text() == "已复制：main的拼写、音标和所有释义"
    numeric(qtbot, 3)
    assert decode(clip()) == entry.related[1]
    assert table.model().rows[0].hidden
    table.revealRequested.emit(entry.id, True)
    window.move_related_word(entry.id, 1, 0)
    child = next(i for i, row in enumerate(table.model().rows) if row.owner == 0 and row.sense == 1)
    point = table.visualRect(table.model().index(child, table.action_column)).center()
    qtbot.mouseClick(table.viewport(), RIGHT, pos=point)
    assert decode(clip()) == entry.related[1]
    table.select_entry(entry.id)
    numeric(qtbot, 2)
    assert decode(clip()) == entry.related[1]
    before_notice = window.toast.label.text()
    numeric(qtbot, 9)
    assert decode(clip()) == entry.related[1]
    assert window.toast.label.text() == before_notice

    for row_number, column, expected in [
            (0, 0, entry.text), (1, 1, entry.text), (child, 0, entry.related[1].text),
            (child, 1, entry.related[1].text), (child, table.action_column, entry.related[1].text)]:
        index = table.model().index(row_number, column)
        table.scrollTo(index)
        clip("sentinel")
        qtbot.mouseClick(table.viewport(), RIGHT, pos=table.visualRect(index).center())
        assert decode(clip()).text == expected

    table.scrollTo(action_index)
    point = action_point(table, action_index, "copy")
    clip("sentinel")
    qtbot.mouseClick(table.viewport(), RIGHT, pos=point)
    assert clip() == "sentinel"
    rect = table.visualRect(action_index)
    qtbot.mouseClick(table.viewport(), RIGHT, pos=QPoint(rect.right() - 2, rect.center().y()))
    assert decode(clip()).text == entry.text


def test_focus_routes_form_and_list_and_keeps_search_inert(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.refresh()
    window.table.select_entry(entry.id)
    form = WordEntry("draft", [Sense("n.", ""), Sense("v.", "verb")],
                     [RelatedWord("draft child", [Sense("n.", "")])])
    window.reset_form(form)
    main, child = window.blocks
    child.phonetic.setFocus()
    numeric(qtbot, 2, CTRL | Qt.KeyboardModifier.KeypadModifier)
    assert decode(clip()) == form.related[0]
    assert not main.rows[0].copy_button.isHidden() and main.rows[1].copy_button.isHidden()
    qtbot.mouseClick(main.rows[0].copy_button, LEFT)
    assert decode(clip()) == RelatedWord(form.text, form.senses)
    main.move_sense(0, 1)
    assert not main.rows[0].copy_button.isHidden() and main.rows[1].copy_button.isHidden()
    child.rows[0].pos.setCurrentIndex(-1)
    numeric(qtbot, 2)
    assert window.toast.label.text() == "整词复制失败：释义词性不能为空"
    assert decode(clip()).text == form.text
    numeric(qtbot, 9)
    assert window.toast.label.text() == "整词复制失败：释义词性不能为空"
    assert decode(clip()).text == form.text
    window.search_box.setFocus()
    numeric(qtbot, 1)
    assert decode(clip()).text == form.text
    window.global_hide_button.setFocus()
    numeric(qtbot, 1)
    assert decode(clip()).text == entry.text
    assert window.filter_name == "pending" and repo.get(entry.id) == entry
    window.table.clearSelection()
    numeric(qtbot, 1)
    assert window.toast.label.text() == "已复制：main的拼写、音标和所有释义"
    assert decode(clip()).text == entry.text


def test_form_right_click_hits_only_blank_word_columns(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.reset_form(entry)
    qtbot.wait(20)
    for block in window.blocks[:2]:
        window.form_scroll.ensureWidgetVisible(block)
        for widget in (block.word, block.phonetic, block.rows[0].copy_button):
            assert block.rows[0].copy_button.toolTip() == copy_word_tooltip()
            x = widget.mapTo(block, widget.rect().center()).x()
            last = block.rows[-1]
            y = last.mapTo(block, last.rect().center()).y()
            clip("sentinel")
            qtbot.mouseClick(block, RIGHT, pos=QPoint(x, y))
            assert decode(clip()) == block.value()

    block = window.blocks[0]
    window.form_scroll.ensureWidgetVisible(block)
    clip("sentinel")
    for widget in (block.word, block.phonetic, block.rows[0].copy_button, block.rows[1].translation):
        point = widget.mapTo(block, widget.rect().center())
        qtbot.mouseClick(block, RIGHT, pos=point)
        assert clip() == "sentinel"


def test_form_copy_from_open_pos_dropdown(window, qtbot):
    window.reset_form(WordEntry("draft", [Sense("n.", "")]))
    pos = window.blocks[0].rows[0].pos
    pos.setFocus()
    pos.showPopup()
    qtbot.waitUntil(lambda: QApplication.activePopupWidget() is not None)
    numeric(qtbot, 1)
    assert decode(clip()) == RelatedWord("draft", [Sense("n.", "")])
    assert QApplication.activePopupWidget() is not None
    qtbot.keyClick(pos.view(), Qt.Key.Key_2)
    assert pos.currentData() == "v."


def test_list_copy_from_open_pos_picker(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.reveal(entry.id, True)
    table = window.table
    index = window.model.index(0, 2)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    qtbot.waitUntil(lambda: QApplication.activePopupWidget() is editor.popup)
    editor.view().setCurrentRow(2)
    numeric(qtbot, 1)
    assert decode(clip()).senses[0].pos == "adj."
    assert repo.get(entry.id) == entry and QApplication.activePopupWidget() is editor.popup
    qtbot.keyClick(editor.view(), Qt.Key.Key_2)
    assert repo.get(entry.id).senses[0].pos == "v."


def test_completed_study_copy_and_paste_preserve_progress(window, repo, qtbot, monkeypatch):
    entry = entry_with_children(repo)
    context = StudyContext(date(2026, 11, 6), False)
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: context))
    window.set_filter("study17")
    page = window.study_page
    page.complete(entry.id, "study")
    page.toggle_completed(True)
    before = repo.study_backup()
    table = page.table
    action = page.model.index(0, page.model.action_column)
    assert [name for name, _ in table.action_rects(table.visualRect(action))] == ["copy"]
    qtbot.mouseClick(table.viewport(), LEFT, pos=action_point(table, action, "copy"))
    assert decode(clip()) == RelatedWord(entry.text, entry.senses)
    index = page.model.index(0, 1)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    paste(qtbot, editor)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: page.model.rows[0].word.text == REPLACEMENT.text)
    after = repo.study_backup()
    assert repo.get(entry.id).related == entry.related
    assert before.task_history == after.task_history
    assert list(before.records.values()) == list(after.records.values())
    assert entry.id in page.model.study_completed_ids


@pytest.mark.parametrize("field_name,owner", [("word", 0), ("phonetic", 1), ("translation", 1)])
def test_form_paste_replaces_only_target_and_restores_focus(window, repo, qtbot, field_name, owner):
    entry = entry_with_children(repo)
    window.reset_form(entry)
    block = window.blocks[owner]
    field = block.rows[1].translation if field_name == "translation" else getattr(block, field_name)
    paste(qtbot, field)
    assert block.value() == REPLACEMENT
    shorter = RelatedWord("short", [Sense("adj.", "")])
    paste(qtbot, block.rows[-1].translation, shorter)
    assert block.value() == shorter
    assert block.rows[-1].translation.hasFocus()
    assert window.edit_id == entry.id and window.star_button.isChecked() and window.is_dirty()
    assert window.blocks[2].value() == entry.related[1]
    assert repo.get(entry.id) == entry
    field = block.word
    field.setFocus()
    field.selectAll()
    clip(json.dumps({"format": FORMAT, "version": 2}))
    qtbot.keyClick(field, Qt.Key.Key_V, CTRL)
    assert field.text() == "short"
    assert window.toast.label.text() == "整词剪贴板内容无效，无法粘贴"
    clip("ordinary")
    qtbot.keyClick(field, Qt.Key.Key_V, CTRL)
    assert field.text() == "ordinary"


def test_form_escape_restores_only_the_last_paste_per_word(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.reset_form(entry)
    main, child, other = window.blocks
    main.word.setText("unsaved")
    original_main, original_child = main.value(), child.value()
    paste(qtbot, main.word)
    main.phonetic.setText("between pastes")
    before_latest = main.value()
    shorter = RelatedWord("shorter", [Sense("n.", "one")])
    paste(qtbot, main.rows[-1].translation, shorter)
    assert main.rows[0].translation.hasFocus()
    paste(qtbot, child.word, shorter)
    other.word.setText("other edit")
    pos = main.rows[0].pos
    pos.setFocus()
    qtbot.keyClick(pos, Qt.Key.Key_Escape)
    assert len(main.rows) == 1 and not pos.currentData()
    main.word.setFocus()
    qtbot.keyClick(main.word, Qt.Key.Key_Escape)
    assert main.value() == before_latest and main.value() != original_main
    assert child.value() == shorter and other.word.text() == "other edit"
    qtbot.keyClick(main.word, Qt.Key.Key_Escape)
    assert main.value() == before_latest
    child.word.setFocus()
    qtbot.keyClick(child.word, Qt.Key.Key_Escape)
    assert child.value() == original_child and other.word.text() == "other edit"
    assert repo.get(entry.id) == entry


@pytest.mark.parametrize("field_name,owner", [("word", -1), ("phonetic", 0), ("translation", 0)])
def test_inline_copy_staged_edit_cancel_and_atomic_save(window, repo, qtbot, field_name, owner):
    entry = entry_with_children(repo)
    window.reveal(entry.id, True)
    table = window.table
    column = 3 if field_name == "translation" else 1
    row = next(i for i, row in enumerate(window.model.rows) if row.owner == owner)
    index = window.model.index(row, column)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    field = editor.phonetic if field_name == "phonetic" else editor
    paste(qtbot, field)
    assert repo.get(entry.id) == entry
    projected = [item for item in window.model.rows if item.owner == owner]
    assert len(projected) == len(REPLACEMENT.senses)
    assert projected[0].word.text == REPLACEMENT.text
    assert projected[0].word.phonetic == REPLACEMENT.phonetic
    assert [item.word.senses[item.sense] for item in projected] == REPLACEMENT.senses
    if column == 1:
        field.setText("revised")
    else:
        field.setPlainText("revised")
    numeric(qtbot, owner + 2)
    staged = decode(clip())
    assert staged == (RelatedWord("revised", REPLACEMENT.senses, REPLACEMENT.phonetic)
                      if field_name == "word" else RelatedWord(REPLACEMENT.text, REPLACEMENT.senses, "revised")
                      if field_name == "phonetic" else
                      RelatedWord(REPLACEMENT.text, [Sense("v.", "revised"), *REPLACEMENT.senses[1:]],
                                  REPLACEMENT.phonetic))
    assert repo.get(entry.id) == entry and table.delegate.editor is editor
    qtbot.keyClick(field, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: table.delegate.editor is None)
    assert repo.get(entry.id) == entry
    qtbot.wait(1)
    index = window.model.index(row, column)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    field = editor.phonetic if field_name == "phonetic" else editor
    paste(qtbot, field)
    if field_name == "translation":
        window.search_box.setFocus()
    else:
        modifier = CTRL if field_name == "phonetic" else Qt.KeyboardModifier.NoModifier
        qtbot.keyClick(field, Qt.Key.Key_Return, modifier)
    qtbot.waitUntil(lambda: table.delegate.editor is None)
    saved = repo.get(entry.id)
    assert saved.id == entry.id and saved.sort_order == entry.sort_order and saved.important == entry.important
    if owner == -1:
        assert RelatedWord(saved.text, saved.senses, saved.phonetic) == REPLACEMENT
        assert saved.related == entry.related
    else:
        assert saved.text == entry.text and saved.senses == entry.senses
        assert saved.related == [REPLACEMENT, entry.related[1]]


def test_inline_smaller_payload_tab_and_failed_save(window, repo, qtbot):
    entry = entry_with_children(repo)
    repo.save(WordEntry("duplicate", [Sense("n.", "")]))
    window.reveal(entry.id, True)
    table = window.table
    index = window.model.index(1, 3)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    duplicate = RelatedWord("Duplicate", [Sense("n.", "duplicate text")])
    paste(qtbot, editor, duplicate)
    editor = table.delegate.editor
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert table.delegate.editor is editor and editor.toPlainText() == "duplicate text"
    assert window.model.rows[0].word.text == "Duplicate"
    assert repo.get(entry.id) == entry
    shorter = RelatedWord("shorter", [Sense("v.", "final")])
    paste(qtbot, editor, shorter)
    editor = table.delegate.editor
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    row = table.delegate.edit_index.data(Qt.ItemDataRole.UserRole)
    assert (row.owner, row.sense, table.delegate.edit_index.column()) == (0, 0, 1)
    assert repo.get(entry.id).senses == shorter.senses
    table.delegate.cancel()


def test_clearing_translation_after_whole_word_paste_removes_only_that_sense(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.reveal(entry.id, True)
    table = window.table
    table.edit(table._entry_index(entry.id, 0, 0, 3))
    editor = table.delegate.editor
    paste(qtbot, editor)
    editor.setPlainText(" \n ")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert table.delegate.editor is None
    saved = repo.get(entry.id)
    assert saved.related == [RelatedWord(REPLACEMENT.text, REPLACEMENT.senses[1:], REPLACEMENT.phonetic),
                             entry.related[1]]


@pytest.mark.parametrize("owner", [-1, 0], ids=["main", "related"])
def test_inline_preview_moves_deleted_editor_and_esc_restores_original_row(window, repo, qtbot, owner):
    definitions = [Sense("n.", f"saved {i}") for i in range(4)]
    entry = repo.save(WordEntry("main", definitions,
                                [RelatedWord("child", definitions), RelatedWord("sibling", [Sense("n.", "")])]))
    other = repo.save(WordEntry("other", [Sense("n.", "hidden")]))
    window.reveal(entry.id, True)
    table = window.table
    original = table._entry_index(entry.id, owner, 3, 3)
    table.setCurrentIndex(original)
    table.edit(original)
    old_editor = table.delegate.editor
    paste(qtbot, old_editor)
    editor = table.delegate.editor
    current = table.currentIndex().data(Qt.ItemDataRole.UserRole)
    assert editor is not old_editor and editor.hasFocus()
    assert (current.owner, current.sense, table.currentIndex().column()) == (owner, 2, 3)
    assert table.delegate.edit_index.row() == table.currentIndex().row()
    assert editor.toPlainText() == REPLACEMENT.senses[-1].translation
    assert len([row for row in window.model.rows if row.entry.id == entry.id and row.owner == owner]) == 3
    hidden = table._entry_index(other.id).row()
    assert table.columnSpan(hidden, 2) == 2
    editor.setPlainText("current final translation")
    numeric(qtbot, owner + 2)
    assert decode(clip()).senses[-1].translation == "current final translation"
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    assert table.delegate.editor is None and table.hasFocus()
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).sense == 3
    assert table.currentIndex().data() == definitions[-1].translation
    assert len([row for row in window.model.rows if row.entry.id == entry.id and row.owner == owner]) == 4
    assert table.columnSpan(table._entry_index(other.id).row(), 2) == 2
    assert repo.get(entry.id) == entry


def test_inline_preview_preserves_hidden_mask(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.refresh()
    table = window.table
    index = window.model.index(0, 1)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    paste(qtbot, editor.phonetic)
    assert window.model.rowCount() == 1 and window.model.rows[0].hidden
    assert window.model.index(0, 1).data() == REPLACEMENT.text
    assert window.model.rows[0].word.phonetic == REPLACEMENT.phonetic
    assert window.model.index(0, 3).data() is None and table.columnSpan(0, 2) == 2
    assert editor.phonetic.hasFocus()
    qtbot.keyClick(editor.phonetic, Qt.Key.Key_Escape)
    assert window.model.rows[0].hidden and window.model.index(0, 1).data() == entry.text


def test_mouse_copy_during_inline_edit_keeps_draft_open(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.reveal(entry.id, True)
    table = window.table
    index = window.model.index(0, 1)
    table.setCurrentIndex(index)
    table.edit(index)
    editor = table.delegate.editor
    editor.phonetic.setFocus()
    editor.setText("pending")
    action_index = window.model.index(0, 4)
    qtbot.mouseClick(table.viewport(), LEFT, pos=action_point(table, action_index, "copy"))
    assert decode(clip()).text == "pending"
    qtbot.wait(20)
    assert table.delegate.editor is editor and repo.get(entry.id) == entry
    editor.setText("")
    numeric(qtbot, 1)
    assert window.toast.label.text() == "整词复制失败：单词拼写不能为空"
    assert decode(clip()).text == "pending"


def test_whole_word_copy_cancels_delayed_name_copy(window, repo, qtbot):
    entry = entry_with_children(repo)
    window.reveal(entry.id, True)
    table = window.table
    index = window.model.index(0, 1)
    qtbot.mouseClick(table.viewport(), LEFT, pos=table.visualRect(index).center())
    assert table._copy_timer.isActive()
    numeric(qtbot, 1)
    assert decode(clip()).text == entry.text
    assert not table._copy_timer.isActive()


@pytest.mark.parametrize("minimum", [False, True], ids=["default", "minimum"])
def test_copy_layout_at_window_widths(window, repo, qtbot, monkeypatch, minimum):
    if minimum:
        window.resize(window.minimumSize())
    entry = entry_with_children(repo)
    window.reveal(entry.id, True)
    window.reset_form(entry)
    qtbot.wait(30)
    table = window.table
    index = window.model.index(0, 4)
    action_cell = table.visualRect(index)
    assert action_cell.right() < table.viewport().width()
    targets = dict(table.action_rects(action_cell, index.data(Qt.ItemDataRole.UserRole)))
    assert all(action_cell.contains(target) for target in targets.values())
    assert all(not targets["copy"].intersects(rect) for name, rect in targets.items() if name != "copy")
    assert targets["drag"].right() < targets["copy"].left() < targets["edit"].left()
    translations = [row.translation.width() for block in window.blocks for row in block.rows]
    assert len(set(translations)) == 1 and translations[0] > 0
    for block in window.blocks:
        row = block.rows[0]
        assert row.translation.geometry().right() < row.copy_button.geometry().left()
        assert row.copy_button.geometry().right() < row.drag_handle.geometry().left()
    # Geometry and hit-testing also run on the Windows Qt backend without
    # accessing the system clipboard. Clipboard round trips are tested above.
    table.wholeWordCopyRequested.disconnect(window.copy_whole_word)
    with qtbot.waitSignal(table.wholeWordCopyRequested) as request:
        qtbot.mouseClick(table.viewport(), LEFT, pos=targets["copy"].center())
    assert request.args == [RelatedWord(entry.text, entry.senses)]
    with qtbot.waitSignal(table.wholeWordCopyRequested) as request:
        qtbot.mouseClick(table.viewport(), RIGHT, pos=QPoint(action_cell.right() - 2, action_cell.center().y()))
    assert request.args == [RelatedWord(entry.text, entry.senses)]
    copied = []
    monkeypatch.setattr(window, "copy_whole_word", copied.append)
    main = window.blocks[0]
    window.form_scroll.ensureWidgetVisible(main)
    icon = main.rows[0].copy_button
    x = icon.mapTo(main, icon.rect().center()).x()
    y = main.rows[1].mapTo(main, main.rows[1].rect().center()).y()
    qtbot.mouseClick(main, RIGHT, pos=QPoint(x, y))
    assert copied == [main.value()]


def test_targeted_storage_replacement_rolls_back_and_rejects_stale_order(repo):
    entry = entry_with_children(repo)
    context = StudyContext(date(2026, 11, 6), False)
    repo.prepare_study(context)
    backup = repo.study_backup()
    with repo.connection:
        repo.connection.execute("""CREATE TRIGGER reject_paste BEFORE INSERT ON senses
            WHEN NEW.translation = 'reject' BEGIN SELECT RAISE(ABORT, 'no paste'); END""")
    with pytest.raises(Exception, match="no paste"):
        repo.replace_word_content(entry, 0, RelatedWord("changed", [Sense("n.", "reject")]))
    assert repo.get(entry.id) == entry and repo.study_backup() == backup
    repo.move_related(entry.id, 0, 1)
    with pytest.raises(ValueError, match="单词内容已变化"):
        repo.replace_word_content(entry, 0, REPLACEMENT)
    assert repo.get(entry.id).related == list(reversed(entry.related))


def test_reserved_chords_startup_repair_and_recorder_rollback(window, repo, qtbot, monkeypatch):
    from vocabulary.window import MainWindow

    repo.write_shortcuts({"add_sense": "Ctrl+9", "remove_sense": "Alt+Num+7",
                          "save": "Ctrl+J", "important": "Ctrl+Return", "add_related": "Alt+N"})
    upgraded = MainWindow(repo)
    qtbot.addWidget(upgraded)
    values = upgraded.shortcuts.values
    assert values["add_sense"] == "Ctrl+J" and values["remove_sense"] == "Ctrl+K"
    assert values["save"] == "Ctrl+Shift+Return"
    assert values["important"] == "Ctrl+Return" and values["add_related"] == "Alt+N"
    reopened = Repository(repo.path)
    try:
        assert reopened.shortcuts() == values
    finally:
        reopened.close()
    for chord in RESERVED_DIGITS:
        with pytest.raises(ValidationError, match="保留"):
            upgraded.shortcuts.update(dict(values, add_sense=chord))
    messages = []
    monkeypatch.setattr("vocabulary.shortcuts.QMessageBox.critical", lambda *args: messages.append(args[-1]))
    dialog = SettingsDialog(upgraded.shortcuts, upgraded)
    qtbot.addWidget(dialog)
    for chord in ("Alt+9", DEFAULT_SHORTCUTS["remove_sense"]):
        dialog.record("add_sense")
        dialog.capture("add_sense", chord)
        assert dialog.editors["add_sense"].text() == "Ctrl+J"
        assert dialog._recording is None and repo.shortcuts() == values
    assert "保留" in messages[0] and "重复" in messages[1]

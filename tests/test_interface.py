import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QMessageBox

from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.shortcuts import SettingsDialog, normalize_shortcut
from vocabulary.widgets import PosCombo


def seed(repo):
    return repo.save(WordEntry("record", [Sense("n.", "记录；唱片\n另一行"), Sense("v.", "记录")],
                               [RelatedWord("recorder", [Sense("n.", "录音机")])]))


def fill_form(window, text="affect"):
    block = window.blocks[0]
    block.word.setText(text)
    block.rows[0].pos.set_pos("v.")
    block.rows[0].translation.setPlainText("影响")


def test_window_starts_empty_and_new_word_defaults_important(window):
    assert window.model.rowCount() == 0
    assert window.star_button.isChecked()
    assert not window.is_dirty()
    assert window.filter_name == "pending"


def test_invalid_save_uses_timed_floating_notice_without_moving_form(window, qtbot):
    field = window.blocks[0].word
    field.setFocus()
    geometry = window.form_scroll.geometry()
    assert not window._save_form()
    assert window.toast.label.text() == "请先输入主词。"
    assert window.toast.isVisible() and field.hasFocus()
    initial_y = window.toast.y()
    qtbot.waitUntil(lambda: window.toast.opacity.opacity() > 0.99, timeout=1000)
    assert window.toast.y() < initial_y
    assert window.toast.geometry().bottom() < window.save_button.mapTo(
        window.centralWidget(), window.save_button.rect().topLeft()).y()
    assert window.form_scroll.geometry() == geometry
    qtbot.waitUntil(lambda: not window.toast.isVisible(), timeout=3000)
    assert field.hasFocus() and window.form_scroll.geometry() == geometry


def test_repeated_floating_notice_replaces_message_and_restarts_duration(window, qtbot):
    window.show_notice("第一条提示", True)
    qtbot.wait(1200)
    window.show_notice("第二条提示", True)
    qtbot.wait(1300)
    assert window.toast.isVisible()
    assert window.toast.label.text() == "第二条提示"
    window.toast.dismiss()


def test_click_word_copies_full_name_with_bottom_notice(window, repo, qtbot):
    from PySide6.QtCore import QPoint

    saved = seed(repo)
    window.refresh()
    clipboard = QApplication.clipboard()
    rect = window.table.visualRect(window.model.index(0, 1))
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                    pos=rect.topLeft() + QPoint(15, 14))
    assert clipboard.text() == "record"
    assert window.model.rows[0].hidden  # Copying does not reveal answers.
    assert window.toast.label.text() == "已复制：record"
    assert window.toast.isVisible() and not window.toast.action.isVisible()
    qtbot.waitUntil(lambda: window.toast.opacity.opacity() > 0.99, timeout=1000)
    root = window.centralWidget()
    assert abs(window.toast.geometry().center().x() - root.rect().center().x()) <= 1
    assert 18 <= root.height() - window.toast.geometry().bottom() <= 22

    window.reveal(saved.id, True)
    rect = window.table.visualRect(window.model.index(2, 1))
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                    pos=rect.topLeft() + QPoint(31, 10))
    assert clipboard.text() == "recorder"
    assert window.toast.label.text() == "已复制：recorder"
    assert window.table.currentIndex() == window.model.index(2, 1)

    long_word = "VeryLongWord" * 8
    repo.save(WordEntry(long_word, [Sense("n.", "长词")]))
    window.refresh()
    rect = window.table.visualRect(window.model.index(3, 1))
    # The ellipsis is clickable, but the clipboard receives the unabridged name.
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                    pos=QPoint(rect.right() - 14, rect.top() + 14))
    assert clipboard.text() == long_word


def test_copy_requires_left_click_on_visible_word_text(window, repo, qtbot):
    from PySide6.QtCore import QPoint

    saved = seed(repo)
    window.reveal(saved.id, True)
    clipboard = QApplication.clipboard()
    clipboard.setText("unchanged")
    table = window.table
    main = table.visualRect(window.model.index(0, 1))
    child = table.visualRect(window.model.index(2, 1))
    for point in (
            main.topLeft() + QPoint(2, 14),  # Left padding.
            main.topLeft() + QPoint(15, 2),  # Above the text.
            QPoint(main.right() - 5, main.center().y()),  # Trailing whitespace.
            child.topLeft() + QPoint(16, 10),  # Related-word arrow.
            table.visualRect(window.model.index(1, 1)).center(),  # Extra sense.
            table.visualRect(window.model.index(0, 2)).center(),  # Part of speech.
            table.visualRect(window.model.index(0, 3)).center(),  # Translation.
    ):
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert clipboard.text() == "unchanged"
        assert not window.toast.isVisible()
    for button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
        qtbot.mouseClick(table.viewport(), button, pos=main.topLeft() + QPoint(15, 14))
        assert clipboard.text() == "unchanged"
        assert not window.toast.isVisible()


@pytest.mark.parametrize("page", ["pending", "all", "learned", "study17"])
def test_copy_works_in_all_lists_with_form_hidden_or_collapsed(window, repo, qtbot, page):
    from PySide6.QtCore import QPoint

    saved = seed(repo)
    if page == "learned":
        repo.set_flag(saved.id, "learned", True)
    if page == "study17":
        from datetime import date
        from vocabulary.learning import StudyContext

        context = StudyContext(date(2026, 10, 30), False)
        repo.prepare_study(context)
        window.set_filter(page)
        window.study_page.load(context)
        table = window.study_page.table
    else:
        window.set_filter(page)
        window.refresh()
        window.set_form_collapsed(True)
        table = window.table
    QApplication.clipboard().setText("unchanged")
    qtbot.wait(20)
    rect = table.visualRect(table.model().index(0, 1))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                    pos=rect.topLeft() + QPoint(15, 14))
    assert QApplication.clipboard().text() == "record"
    assert window.toast.isVisible()
    assert window.toast.label.text() == "已复制：record"


def test_collapse_preserves_draft_focus_and_automatic_height(window, qtbot):
    fill_form(window)
    window.add_related()
    qtbot.wait(20)
    child = window.blocks[-1]
    child.word.setText("related-draft")
    child.rows[0].translation.setFocus()
    draft = window.form_value()
    expanded_height = window.editor_panel.height()
    qtbot.mouseClick(window.collapse_button, Qt.MouseButton.LeftButton)
    qtbot.wait(20)
    assert window.form_collapsed and not window.editor_body.isVisible()
    assert window.editor_panel.height() <= 60
    assert window.form_value() == draft and window.is_dirty()
    assert window.collapse_button.hasFocus()
    qtbot.keyClick(window.collapse_button, Qt.Key.Key_Space)
    qtbot.wait(30)
    assert not window.form_collapsed and window.editor_body.isVisible()
    assert abs(window.editor_panel.height() - expanded_height) <= 2
    assert window.form_value() == draft and child.rows[0].translation.hasFocus()


def test_form_grows_with_senses_then_scrolls_at_half_window_and_shrinks(window, qtbot):
    initial = window.editor_panel.height()
    assert 180 <= initial < 250
    assert window.form_scroll.verticalScrollBar().maximum() == 0
    block = window.blocks[0]
    for _ in range(3):
        block.add_sense()
    qtbot.waitUntil(lambda: window.editor_panel.height() > initial)
    assert window.editor_panel.height() < window.centralWidget().height() // 2
    qtbot.waitUntil(lambda: window.form_scroll.verticalScrollBar().maximum() == 0)
    for _ in range(6):
        block.add_sense()
    qtbot.waitUntil(lambda: window.form_scroll.verticalScrollBar().maximum() > 0)
    assert window.editor_panel.height() == window.centralWidget().height() // 2
    for _ in range(9):
        block.remove_last_sense()
    qtbot.waitUntil(lambda: window.editor_panel.height() == initial)
    qtbot.waitUntil(lambda: window.form_scroll.verticalScrollBar().maximum() == 0)


def test_related_words_grow_form_and_resize_recalculates_half_window_limit(window, qtbot):
    initial = window.editor_panel.height()
    for _ in range(7):
        window.add_related()
    qtbot.waitUntil(lambda: window.form_scroll.verticalScrollBar().maximum() > 0)
    assert window.editor_panel.height() == window.centralWidget().height() // 2
    window.resize(1100, 1100)
    qtbot.waitUntil(lambda: window.editor_panel.height() == window.centralWidget().height() // 2)
    window.resize(860, 640)
    qtbot.waitUntil(lambda: window.editor_panel.height() == window.centralWidget().height() // 2)
    assert window.height() == 640
    assert window.save_button.isVisible()
    window.reset_form()
    qtbot.waitUntil(lambda: window.editor_panel.height() == initial)
    assert window.form_scroll.verticalScrollBar().maximum() == 0


def test_dragging_between_panels_does_not_resize_form(window, qtbot):
    from PySide6.QtCore import QPoint
    height = window.editor_panel.height()
    boundary = window.editor_panel.mapTo(window.centralWidget(), QPoint(100, -5))
    qtbot.mousePress(window.centralWidget(), Qt.MouseButton.LeftButton, pos=boundary)
    qtbot.mouseMove(window.centralWidget(), boundary - QPoint(0, 100))
    qtbot.mouseRelease(window.centralWidget(), Qt.MouseButton.LeftButton,
                       pos=boundary - QPoint(0, 100))
    assert window.editor_panel.height() == height


def test_form_actions_expand_collapsed_panel_and_duplicate_toast_locates_word(window, repo, qtbot):
    saved = seed(repo)
    window.set_form_collapsed(True)
    qtbot.keyClick(window.table, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(30)
    assert not window.form_collapsed and len(window.blocks) == 2
    window.reset_form()
    window.set_form_collapsed(True)
    window.list_action(saved.id, "edit")
    assert not window.form_collapsed and window.edit_id == saved.id
    window.reset_form()
    fill_form(window, "RECORD")
    assert not window._save_form()
    assert window.toast.action.isVisible()
    qtbot.mouseClick(window.toast.action, Qt.MouseButton.LeftButton)
    assert not window.toast.isVisible()
    assert window.filter_name == "all"
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == saved.id


def test_pos_digits_space_and_escape(qtbot):
    combo = PosCombo()
    qtbot.addWidget(combo)
    combo.show()
    combo.setFocus()
    qtbot.keyClick(combo, Qt.Key.Key_3)
    assert combo.currentData() == "adj."
    qtbot.keyClick(combo, Qt.Key.Key_Space)
    assert combo.view().isVisible()
    assert combo.itemText(3).startswith("3")
    qtbot.keyClick(combo.view(), Qt.Key.Key_Down)
    qtbot.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert combo.currentData() == "adj."
    combo.showPopup()
    qtbot.keyClick(combo.view(), Qt.Key.Key_2)
    assert combo.currentData() == "v."
    assert not combo.view().isVisible()


def test_save_resets_form_and_reloads_data(window, repo, qtbot):
    fill_form(window)
    assert window._save_form()
    assert repo.list_entries()[0].text == "affect"
    assert repo.list_entries()[0].important
    assert window.blocks[0].word.text() == ""
    assert window.star_button.isChecked()
    assert not window.is_dirty()


def test_duplicate_keeps_input_and_locates_existing(window, repo):
    saved = seed(repo)
    fill_form(window, " RECORD ")
    assert not window._save_form()
    assert window.blocks[0].word.text() == " RECORD "
    window.locate_duplicate()
    assert window.filter_name == "all"
    assert window.model.rows[window.table.currentIndex().row()].entry.id == saved.id


def test_mask_single_summary_expand_hide(window, repo, qtbot):
    saved = seed(repo)
    window.refresh()
    assert window.model.rowCount() == 1
    assert window.table.columnSpan(0, 2) == 2
    index = window.model.index(0, 2)
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.table.visualRect(index).center())
    assert window.model.rowCount() == 3
    assert not any(row.hidden for row in window.model.rows)
    index = window.model.index(2, 3)
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier, window.table.visualRect(index).center())
    assert window.model.rowCount() == 1 and window.model.rows[0].hidden


def test_selecting_related_sense_selects_only_its_whole_entry(window, repo, qtbot):
    saved = seed(repo)
    other = repo.save(WordEntry("other", [Sense("adj.", "其他的")]))
    window.reveal(saved.id, True)
    window.reveal(other.id, True)
    index = window.model.index(2, 3)
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.table.visualRect(index).center())
    assert {item.row() for item in window.table.selectionModel().selectedRows()} == {0, 1, 2}
    assert window.table.currentIndex() == index

    for number in (0, 1, 2):
        index = window.model.index(number, 1)
        qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.table.visualRect(index).center())
        assert {item.row() for item in window.table.selectionModel().selectedRows()} == {0, 1, 2}
        assert window.table.currentIndex() == index

    # The continuous highlight must also follow keyboard and programmatic selection.
    qtbot.keyClick(window.table, Qt.Key.Key_Down)
    assert {item.row() for item in window.table.selectionModel().selectedRows()} == {3}
    window.table.setCurrentIndex(window.model.index(1, 2))
    assert {item.row() for item in window.table.selectionModel().selectedRows()} == {0, 1, 2}


def test_group_selection_survives_reveal_and_refresh(window, repo):
    saved = seed(repo)
    window.refresh()
    window.table.setCurrentIndex(window.model.index(0, 1))
    window.reveal(saved.id, True)
    assert {item.row() for item in window.table.selectionModel().selectedRows()} == {0, 1, 2}
    window.refresh()
    assert {item.row() for item in window.table.selectionModel().selectedRows()} == {0, 1, 2}
    window.reveal(saved.id, False)
    assert len(window.table.selectionModel().selectedRows()) == 1


@pytest.mark.parametrize("related", [False, True])
@pytest.mark.parametrize("field_name", ["word", "pos", "translation"])
@pytest.mark.parametrize("action", ["add_sense", "add_related"])
@pytest.mark.parametrize("composing", [False, True])
def test_add_shortcuts_work_from_each_form_input(window, qtbot, related, field_name, action, composing):
    if related:
        window.add_related()
        qtbot.wait(10)
    block = window.blocks[-1]
    field = block.word if field_name == "word" else getattr(block.rows[0], field_name)
    field.setFocus()
    qtbot.wait(10)
    assert field.hasFocus()
    if composing:
        QApplication.sendEvent(field, QInputMethodEvent("pin yin", []))
    words_before, senses_before = len(window.blocks), len(block.rows)
    key = Qt.Key.Key_J if action == "add_sense" else Qt.Key.Key_N
    qtbot.keyClick(field, key, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    if action == "add_sense":
        assert len(block.rows) == senses_before + 1
        assert len(window.blocks) == words_before
    else:
        assert len(window.blocks) == words_before + 1
        assert len(block.rows) == senses_before


def test_shortcut_target_and_focus_labels(window, qtbot):
    window.blocks[0].word.setFocus()
    qtbot.wait(10)
    qtbot.keyClick(window.blocks[0].word, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    assert len(window.blocks[0].rows) == 2
    qtbot.keyClick(window.blocks[0].rows[-1].pos, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    assert len(window.blocks) == 2
    child = window.blocks[1]
    assert "Ctrl+J" in child.add_button.text()
    assert "Ctrl+J" not in window.blocks[0].add_button.text()
    qtbot.keyClick(child.word, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    assert len(child.rows) == 2 and len(window.blocks[0].rows) == 2
    qtbot.keyClick(child.rows[-1].pos, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert len(child.rows) == 1
    qtbot.keyClick(child.rows[-1].pos, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert len(child.rows) == 1


@pytest.mark.parametrize("field_name", ["word", "translation"])
@pytest.mark.parametrize("action,key", [("add_sense", Qt.Key.Key_Backspace), ("add_related", Qt.Key.Key_Delete)])
def test_configured_shortcuts_take_priority_over_input_editing(window, qtbot, field_name, action, key):
    sequence = "Ctrl+Backspace" if action == "add_sense" else "Ctrl+Delete"
    window.shortcuts.update(dict(window.shortcuts.values, **{action: sequence}))
    block = window.blocks[0]
    field = block.word if field_name == "word" else block.rows[0].translation
    if field_name == "word":
        field.setText("keep this text")
    else:
        field.setPlainText("keep this text")
    field.setFocus()
    qtbot.keyClick(field, Qt.Key.Key_Home)
    qtbot.keyClick(field, key, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(10)
    assert len(block.rows) == (2 if action == "add_sense" else 1)
    assert len(window.blocks) == (2 if action == "add_related" else 1)
    assert (field.text() if field_name == "word" else field.toPlainText()) == "keep this text"


def test_sense_shortcut_resolves_live_input_focus_instead_of_cached_target(window, qtbot):
    field = window.blocks[0].rows[0].translation
    field.setFocus()
    qtbot.wait(10)
    window.active_block = None
    qtbot.keyClick(field, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    assert len(window.blocks[0].rows) == 2


def test_tab_reverse_newline_and_star_space(window, qtbot):
    block = window.blocks[0]
    block.word.setFocus()
    qtbot.keyClick(block.word, Qt.Key.Key_Tab)
    assert block.rows[0].pos.hasFocus()
    qtbot.keyClick(block.rows[0].pos, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert block.word.hasFocus()
    edit = block.rows[0].translation
    edit.setFocus()
    qtbot.keyClicks(edit, "line")
    qtbot.keyClick(edit, Qt.Key.Key_Return)
    qtbot.keyClicks(edit, "two")
    assert edit.toPlainText() == "line\ntwo"
    window.star_button.setFocus()
    qtbot.keyClick(window.star_button, Qt.Key.Key_Space)
    assert not window.star_button.isChecked()
    qtbot.keyClick(window.star_button, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)
    assert window.star_button.isChecked()


def test_shortcut_update_commits_before_binding_and_changes_hints(window, repo, qtbot, monkeypatch):
    original = window.shortcuts._apply
    observed = []
    def observe(values):
        observed.append(repo.shortcuts()["add_related"])
        original(values)
    monkeypatch.setattr(window.shortcuts, "_apply", observe)
    values = dict(window.shortcuts.values, add_related="Alt+N")
    window.shortcuts.update(values)
    assert observed == ["Alt+N"]
    assert "Alt+N" in window.add_related_button.text()
    main = window.blocks[0].word
    main.setFocus()
    qtbot.keyClick(main, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert len(window.blocks) == 1
    qtbot.keyClick(main, Qt.Key.Key_N, Qt.KeyboardModifier.AltModifier)
    qtbot.wait(10)
    assert len(window.blocks) == 2


def test_bad_shortcuts_and_failed_write_keep_existing_binding(window, repo, monkeypatch):
    old = dict(window.shortcuts.values)
    with pytest.raises(ValidationError):
        window.shortcuts.update(dict(old, add_sense=old["add_related"]))
    assert repo.shortcuts() == old
    def fail(values):
        raise OSError("test write failure")
    monkeypatch.setattr(repo, "write_shortcuts", fail)
    with pytest.raises(OSError):
        window.shortcuts.update(dict(old, add_related="Alt+N"))
    assert window.shortcuts.values == old


@pytest.mark.parametrize("key", ["Ctrl+A", "Alt+F4", "J", "Ctrl+Tab", "Ctrl+Space"])
def test_reserved_shortcuts_rejected(key):
    with pytest.raises(ValidationError):
        normalize_shortcut(key)


def test_learning_and_star_actions_persist_and_restore_via_learned_filter(window, repo):
    saved = seed(repo)
    window.refresh()
    window.list_action(saved.id, "important")
    assert not repo.get(saved.id).important
    window.list_action(saved.id, "learned")
    assert repo.get(saved.id).learned
    assert window.model.rowCount() == 0
    assert not hasattr(window, "undo")
    window.set_filter("learned")
    window.list_action(saved.id, "learned")
    window.set_filter("pending")
    assert not repo.get(saved.id).learned and window.model.rowCount() == 1


def test_inline_translation_save_and_cancel(window, repo, qtbot):
    saved = seed(repo)
    window.reveal(saved.id, True)
    index = window.model.index(0, 3)
    window.table.edit(index)
    qtbot.wait(10)
    editor = window.table.delegate.editor
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("新的多行\n翻译")
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(20)
    assert repo.get(saved.id).senses[0].translation == "新的多行\n翻译"
    window.table.edit(window.model.index(0, 3))
    qtbot.wait(10)
    editor = window.table.delegate.editor
    editor.setPlainText("不保存")
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    qtbot.wait(10)
    assert repo.get(saved.id).senses[0].translation == "新的多行\n翻译"


def test_inline_invalid_content_stays_in_editor(window, repo, qtbot):
    saved = seed(repo)
    window.reveal(saved.id, True)
    window.table.edit(window.model.index(0, 3))
    qtbot.wait(10)
    editor = window.table.delegate.editor
    editor.clear()
    assert not window.table.delegate.submit()
    assert window.table.delegate.editor is editor
    assert repo.get(saved.id).senses[0].translation


def test_full_edit_retains_star_and_cancels_without_write(window, repo):
    saved = seed(repo)
    repo.set_flag(saved.id, "important", False)
    window.list_action(saved.id, "edit")
    assert window.edit_id == saved.id
    assert not window.star_button.isChecked()
    assert len(window.blocks) == 2
    window.reset_form()
    assert not repo.get(saved.id).important


def test_binding_apply_failure_restores_database_and_bindings(window, repo, monkeypatch):
    old = dict(window.shortcuts.values)
    original = window.shortcuts._apply
    def fail_new(values):
        if values["add_related"] == "Alt+N":
            raise RuntimeError("simulated binding failure")
        original(values)
    monkeypatch.setattr(window.shortcuts, "_apply", fail_new)
    with pytest.raises(RuntimeError):
        window.shortcuts.update(dict(old, add_related="Alt+N"))
    assert window.shortcuts.values == repo.shortcuts() == old


def test_setting_recorder_persists_and_escape_cancels(window, repo, qtbot):
    window.shortcuts.set_suspended(True)
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.record("add_related")
    qtbot.keyClick(dialog.editors["add_related"], Qt.Key.Key_N, Qt.KeyboardModifier.AltModifier)
    assert repo.shortcuts()["add_related"] == "Alt+N"
    assert "Alt+N" in window.add_related_button.text()
    dialog.record("add_related")
    qtbot.keyClick(dialog.editors["add_related"], Qt.Key.Key_Escape)
    assert repo.shortcuts()["add_related"] == "Alt+N"
    dialog.restore()
    assert repo.shortcuts()["add_related"] == "Ctrl+N"
    dialog.close()
    window.shortcuts.set_suspended(False)


def answer_confirmation(qtbot, accept):
    def answer():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        if accept:
            assert dialog.defaultButton().text() == "确认移除"
            qtbot.keyClick(dialog.defaultButton(), Qt.Key.Key_Space)
        else:
            qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    QTimer.singleShot(30, answer)


def test_related_removal_confirm_space_and_escape(window, qtbot):
    window.add_related()
    qtbot.wait(10)
    child = window.blocks[-1]
    child.word.setText("keep me")
    answer_confirmation(qtbot, False)
    window.remove_last_related()
    assert len(window.blocks) == 2
    answer_confirmation(qtbot, True)
    window.remove_last_related()
    assert len(window.blocks) == 1
    window.add_related()
    window.remove_last_related()
    assert len(window.blocks) == 1


def test_sense_removal_confirms_and_preserves_one(window, qtbot):
    block = window.blocks[0]
    block.add_sense()
    qtbot.wait(10)
    block.rows[-1].translation.setPlainText("delete me")
    answer_confirmation(qtbot, False)
    block.remove_last_sense()
    assert len(block.rows) == 2
    answer_confirmation(qtbot, True)
    block.remove_last_sense()
    block.remove_last_sense()
    assert len(block.rows) == 1


def test_mouse_double_click_edits_each_kind_and_focus_out_saves(window, repo, qtbot):
    saved = seed(repo)
    window.reveal(saved.id, True)
    for column, text in ((1, "records"), (2, "v."), (3, "更新后的意思")):
        index = window.model.index(0, column)
        qtbot.mouseDClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.table.visualRect(index).center())
        qtbot.wait(20)
        editor = window.table.delegate.editor
        assert editor is not None
        if column == 1:
            editor.setText(text)
        elif column == 2:
            editor.set_pos(text)
        else:
            editor.setPlainText(text)
        window.blocks[0].word.setFocus()
        qtbot.wait(30)
        assert window.table.delegate.editor is None
    current = repo.get(saved.id)
    assert current.text == "records"
    assert current.senses[0].pos == "v."
    assert current.senses[0].translation == "更新后的意思"


def test_editing_related_cell_does_not_change_main(window, repo, qtbot):
    saved = seed(repo)
    window.reveal(saved.id, True)
    window.table.edit(window.model.index(2, 1))
    qtbot.wait(10)
    window.table.delegate.editor.setText("recorders")
    assert window.table.delegate.submit()
    qtbot.wait(20)
    assert repo.get(saved.id).related[0].text == "recorders"
    assert repo.get(saved.id).text == "record"


def test_full_edit_adds_and_removes_aggregate_items(window, repo, qtbot):
    saved = seed(repo)
    window.list_action(saved.id, "edit")
    window.blocks[0].add_sense()
    row = window.blocks[0].rows[-1]
    row.pos.set_pos("adj.")
    row.translation.setPlainText("创纪录的")
    window.add_related()
    child = window.blocks[-1]
    child.word.setText("recording")
    child.rows[0].pos.set_pos("n.")
    child.rows[0].translation.setPlainText("录音")
    assert window._save_form()
    current = repo.get(saved.id)
    assert len(current.senses) == 3 and len(current.related) == 2


def test_delete_icon_flow_cascades(window, repo, qtbot):
    saved = seed(repo)
    window.refresh()
    answer_confirmation(qtbot, True)
    window.list_action(saved.id, "trash")
    assert not repo.list_entries()
    assert repo.connection.execute("SELECT count(*) FROM senses").fetchone()[0] == 0


def test_form_save_shortcut_can_be_remapped(window, repo, qtbot):
    window.shortcuts.update(dict(window.shortcuts.values, save="Alt+S"))
    fill_form(window)
    widget = window.blocks[0].rows[0].translation
    widget.setFocus()
    qtbot.keyClick(widget, Qt.Key.Key_S, Qt.KeyboardModifier.AltModifier)
    assert len(repo.list_entries()) == 1
    assert "Alt+S" in window.save_button.text()


def test_multi_related_focus_scrolls_input_into_view(window, qtbot):
    window.resize(860, 640)
    for _ in range(4):
        window.add_related()
    qtbot.wait(30)
    target = window.blocks[-1].rows[0].translation
    target.setFocus()
    qtbot.wait(40)
    top_left = target.mapTo(window.form_scroll.viewport(), target.rect().topLeft())
    assert top_left.y() >= 0
    assert top_left.y() + target.height() <= window.form_scroll.viewport().height()


@pytest.mark.parametrize("choice", ["保存", "放弃", "取消"])
def test_inline_switch_resolves_bottom_draft(window, repo, qtbot, choice):
    saved = seed(repo)
    window.reveal(saved.id, True)
    fill_form(window, "new-word")
    def answer():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        target = next(item for item in dialog.buttons() if item.text() == choice)
        qtbot.mouseClick(target, Qt.MouseButton.LeftButton)
    QTimer.singleShot(30, answer)
    index = window.model.index(0, 1)
    qtbot.mouseDClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.table.visualRect(index).center())
    qtbot.wait(20)
    if choice == "取消":
        assert window.table.delegate.editor is None
        assert window.blocks[0].word.text() == "new-word"
    else:
        assert window.table.delegate.editor.text() == "record"
        assert len(repo.list_entries()) == (2 if choice == "保存" else 1)


def test_settings_returns_focus_to_previous_form_field(window, qtbot):
    edit = window.blocks[0].rows[0].translation
    edit.setFocus()
    qtbot.wait(10)
    window.settings_button.setFocus()
    def close():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, SettingsDialog)
        dialog.accept()
    QTimer.singleShot(30, close)
    window.open_settings()
    qtbot.wait(10)
    assert edit.hasFocus()


def test_chinese_input_method_preedit_and_commit_do_not_trigger_actions(window, qtbot):
    edit = window.blocks[0].rows[0].translation
    edit.setFocus()
    QApplication.sendEvent(edit, QInputMethodEvent("fan yi", []))
    assert edit.toPlainText() == ""
    committed = QInputMethodEvent()
    committed.setCommitString("翻译内容")
    QApplication.sendEvent(edit, committed)
    assert edit.toPlainText() == "翻译内容"
    assert len(window.blocks) == 1 and len(window.blocks[0].rows) == 1


def test_new_word_appends_expanded_with_related_and_scrolls_to_bottom(window, repo, qtbot):
    old_ids = [repo.save(WordEntry(f"old-{number}", [Sense("n.", "旧词")])).id
               for number in range(30)]
    window.refresh()
    window.table.scrollToTop()
    fill_form(window, "newest")
    window.add_related()
    child = window.blocks[-1]
    child.word.setText("related-newest")
    child.rows[0].pos.set_pos("adj.")
    child.rows[0].translation.setPlainText("新关联词")
    assert window._save_form()
    qtbot.wait(40)
    latest = repo.list_entries()[-1]
    roots = [row for row in window.model.rows if row.first]
    assert [row.entry.id for row in roots] == old_ids + [latest.id]
    assert all(row.hidden for row in roots[:-1])
    assert not roots[-1].hidden
    assert window.model.rows[-1].word.text == "related-newest"
    assert not window.model.rows[-1].hidden
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == latest.id
    bar = window.table.verticalScrollBar()
    assert bar.value() == bar.maximum() and bar.maximum() > 0


def test_saving_from_learned_filter_shows_new_pending_word(window, repo):
    saved = seed(repo)
    repo.set_flag(saved.id, "learned", True)
    window.set_filter("learned")
    fill_form(window, "new-pending")
    assert window._save_form()
    assert window.filter_name == "pending"
    assert window.model.rows[0].entry.text == "new-pending"
    assert not window.model.rows[0].hidden


def test_global_toggle_applies_to_all_filters_and_related_words(window, repo, qtbot):
    pending = seed(repo)
    learned = repo.save(WordEntry("known", [Sense("adj.", "已知的")],
                                  [RelatedWord("know", [Sense("v.", "知道")])]))
    repo.set_flag(learned.id, "learned", True)
    window.refresh()
    assert all(row.hidden for row in window.model.rows)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert not window.global_hide_button.isChecked()
    assert window.revealed == {pending.id, learned.id}
    assert not any(row.hidden for row in window.model.rows)
    window.set_filter("learned")
    assert len(window.model.rows) == 2
    assert not any(row.hidden for row in window.model.rows)
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    assert window.global_hide_button.isChecked()
    assert window.model.rowCount() == 1 and window.model.rows[0].hidden
    window.set_filter("pending")
    assert window.model.rowCount() == 1 and window.model.rows[0].hidden


def test_restart_resets_visibility_after_new_word_and_global_show(window, repo, qtbot):
    from vocabulary.window import MainWindow
    fill_form(window)
    assert window._save_form()
    window.set_global_hidden(False)
    restarted = MainWindow(repo)
    qtbot.addWidget(restarted)
    restarted.show()
    qtbot.wait(20)
    assert restarted.windowTitle() == "kkMemorizeVocabulary"
    assert restarted.global_hide_button.isChecked()
    assert all(row.hidden for row in restarted.model.rows)


def test_global_toggle_keeps_visibility_when_inline_edit_is_invalid(window, repo, qtbot):
    saved = seed(repo)
    window.reveal(saved.id, True)
    window.table.edit(window.model.index(0, 3))
    qtbot.wait(10)
    window.table.delegate.editor.clear()
    qtbot.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
    qtbot.wait(20)
    assert window.global_hide_button.isChecked()
    assert window.global_hidden
    assert not window.model.rows[0].hidden
    assert window.table.delegate.editor is not None
    assert repo.get(saved.id).senses[0].translation


def test_edit_existing_does_not_reveal_or_reorder(window, repo):
    saved = seed(repo)
    newer = repo.save(WordEntry("later", [Sense("adv.", "稍后")]))
    window.refresh()
    window.list_action(saved.id, "edit")
    window.blocks[0].rows[0].translation.setPlainText("已修改")
    assert window._save_form()
    assert [row.entry.id for row in window.model.rows] == [saved.id, newer.id]
    assert all(row.hidden for row in window.model.rows)

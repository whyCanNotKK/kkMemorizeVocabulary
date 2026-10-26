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


def test_learning_and_star_actions_persist_and_undo(window, repo):
    saved = seed(repo)
    window.refresh()
    window.list_action(saved.id, "important")
    assert not repo.get(saved.id).important
    window.list_action(saved.id, "learned")
    assert repo.get(saved.id).learned
    assert window.model.rowCount() == 0
    window.undo_mark()
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

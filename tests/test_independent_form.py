from datetime import date

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from vocabulary.domain import Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.mark.parametrize("page", ["pending", "all", "learned", "study17"])
@pytest.mark.parametrize("editing", [False, True])
def test_list_edit_keeps_form_and_widget_identity(window, repo, qtbot, monkeypatch, page, editing):
    monkeypatch.setattr(StudyContext, "now", classmethod(
        lambda cls: StudyContext(date(2026, 11, 2), False)))
    entry = repo.save(WordEntry("word", [Sense("n.", "saved")], learned=page == "learned"))
    if editing:
        window.reset_form(entry)
    window.blocks[0].word.setText("unfinished")
    block = window.blocks[0]
    draft = window.form_value()
    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("unrelated operation processed draft"))
    window.set_filter(page)
    table = window.current_table()
    table.revealRequested.emit(entry.id, True)
    table.edit(table.model().index(0, 3))
    assert window.blocks[0] is block and window.form_value() == draft
    table.delegate.editor.setPlainText("list change")
    qtbot.keyClick(table.delegate.editor, Qt.Key.Key_Return)
    qtbot.wait(10)
    assert repo.get(entry.id).senses[0].translation == "list change"
    window.set_filter("pending")
    assert window.blocks[0] is block and window.form_value() == draft


def test_same_word_list_edit_and_form_changes_merge_without_lost_updates(window, repo, qtbot):
    entry = repo.save(WordEntry("word", [Sense("n.", "saved")]))
    window.reset_form(entry)
    window.blocks[0].word.setText("renamed in draft")
    window.reveal(entry.id, True)
    window.table.edit(window.model.index(0, 3))
    window.table.delegate.editor.setPlainText("list change")
    qtbot.keyClick(window.table.delegate.editor, Qt.Key.Key_Return)
    assert window.blocks[0].rows[0].translation.toPlainText() == "saved"
    assert window._save_form()
    saved = repo.get(entry.id)
    assert saved.text == "renamed in draft" and saved.senses[0].translation == "list change"


def test_same_part_conflict_is_only_reported_on_explicit_form_save(window, repo, qtbot, monkeypatch):
    entry = repo.save(WordEntry("word", [Sense("n.", "saved")]))
    window.reset_form(entry)
    window.blocks[0].rows[0].translation.setPlainText("draft change")
    draft = window.form_value()
    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("must not interrupt draft"))
    window.reveal(entry.id, True)
    window.table.edit(window.model.index(0, 3))
    window.table.delegate.editor.setPlainText("list change")
    qtbot.keyClick(window.table.delegate.editor, Qt.Key.Key_Return)
    assert window.form_value() == draft
    assert not window._save_form()
    assert window.form_value() == draft and window.is_dirty()
    assert repo.get(entry.id).senses[0].translation == "list change"


@pytest.mark.parametrize("choice", ["保存", "放弃", "取消"])
def test_opening_another_word_preserves_replacement_confirmation(window, repo, qtbot, choice):
    first = repo.save(WordEntry("first", [Sense("n.", "saved")]))
    second = repo.save(WordEntry("second", [Sense("n.", "saved")]))
    window.reset_form(first)
    window.blocks[0].word.setText("changed")

    def answer():
        dialog = QApplication.activeModalWidget()
        next(button for button in dialog.buttons() if button.text() == choice).click()

    QTimer.singleShot(0, answer)
    window.list_action(second.id, "edit")
    assert window.edit_id == (first.id if choice == "取消" else second.id)
    assert repo.get(first.id).text == ("changed" if choice == "保存" else "first")


def test_reopening_same_word_and_flags_do_not_reset_draft(window, repo, monkeypatch):
    entry = repo.save(WordEntry("word", [Sense("n.", "saved")]))
    window.reset_form(entry)
    block = window.blocks[0]
    block.word.setText("unfinished")
    draft = window.form_value()
    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("must not process draft"))
    for action in ("edit", "important", "learned"):
        window.list_action(entry.id, action)
        assert window.blocks[0] is block and window.form_value() == draft
    assert window._save_form()
    assert repo.get(entry.id).learned and not repo.get(entry.id).important

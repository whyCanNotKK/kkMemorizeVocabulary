import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from vocabulary.draft_reset_dialog import DraftResetDialog
from vocabulary.domain import RelatedWord, Sense, WordEntry


def fill(window, repo, editing):
    entry = WordEntry("draft", [Sense("n.", "first"), Sense("v.", "second")],
                      [RelatedWord("child", [Sense("n.", "child meaning")])])
    if editing:
        entry = repo.save(entry)
    window.reset_form(entry)
    window.blocks[0].word.setText("unsaved draft")
    window.star_button.setChecked(False)


@pytest.mark.parametrize("choice", ["confirm", "cancel", "repeat-click", "enter", "escape", "close"])
@pytest.mark.parametrize("editing", [False, True], ids=["clear", "cancel-edit"])
def test_draft_reset_requires_explicit_confirmation_at_anchor(window, repo, qtbot, tmp_path, choice, editing):
    fill(window, repo, editing)
    qtbot.wait(20)
    button = window.cancel_button
    assert button.text() == ("取消" if editing else "清空") and button.isVisible()
    saved_entries = repo.list_entries()
    before = window.form_value()
    blocks = list(window.blocks)
    anchor = QRect(button.mapToGlobal(QPoint()), button.size())
    errors = []

    def answer():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, DraftResetDialog)
            assert ("取消当前编辑？" if editing else "清空当前输入？") in {
                label.text() for label in dialog.findChildren(QLabel)}
            assert {control.text() for control in dialog.findChildren(QPushButton)} == {"确认", "取消"}
            assert dialog.cancel_button.hasFocus() and dialog.cancel_button.isDefault()
            cancel_rect = QRect(dialog.cancel_button.mapToGlobal(QPoint()), dialog.cancel_button.size())
            assert cancel_rect == anchor
            dialog.grab().save(str(tmp_path / "confirmation.png"))
            if choice == "confirm":
                qtbot.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
            elif choice == "cancel":
                qtbot.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
            elif choice == "repeat-click":
                # A second click at the original Clear screen position must hit Cancel.
                point = dialog.mapFromGlobal(anchor.center())
                target = dialog.childAt(point)
                assert target is dialog.cancel_button
                qtbot.mouseClick(target, Qt.MouseButton.LeftButton,
                                pos=target.mapFromGlobal(anchor.center()))
            elif choice == "close":
                dialog.close()
            else:
                qtbot.keyClick(dialog.cancel_button, Qt.Key.Key_Return if choice == "enter" else Qt.Key.Key_Escape)
        except BaseException as error:
            errors.append(error)
            dialog.reject()

    QTimer.singleShot(30, answer)
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not errors, errors
    assert repo.list_entries() == saved_entries
    if choice == "confirm":
        qtbot.waitUntil(window.blocks[0].word.hasFocus)
        assert window.blocks[0].word.text() == "" and len(window.blocks) == 1
        assert len(window.blocks[0].rows) == 1 and window.star_button.isChecked()
        assert not window.is_dirty() and not window.cancel_button.isVisible()
        assert window.edit_id is None and window.form_title.text() == "添加单词"
    else:
        assert window.form_value() == before and window.blocks == blocks and window.is_dirty()


def test_unchanged_existing_word_still_asks_before_cancelling_edit(window, repo, monkeypatch, qtbot):
    saved = repo.save(WordEntry("saved", [Sense("n.", "meaning")]))
    window.reset_form(saved)
    assert window.cancel_button.text() == "取消" and not window.is_dirty()
    monkeypatch.setattr(window, "resolve_dirty", lambda: pytest.fail("cancel edit must not offer saving"))
    QTimer.singleShot(10, lambda: QApplication.activeModalWidget().reject())
    window.cancel_form()
    assert window.edit_id == saved.id and window.blocks[0].word.text() == "saved"
    monkeypatch.undo()
    assert repo.get(saved.id) == saved


def test_empty_new_form_has_nothing_to_clear(window):
    assert window.cancel_button.text() == "清空" and not window.cancel_button.isVisible()
    window.cancel_form()
    assert not window.is_dirty()

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from vocabulary.domain import Sense, WordEntry
from vocabulary.unsaved_input_dialog import UnsavedInputDialog


@pytest.mark.parametrize('operation', ['resolve', 'close', 'edit_another'])
@pytest.mark.parametrize('choice', ['save', 'discard', 'close', 'escape'])
def test_unsaved_input_actions_and_cancel(window, repo, qtbot, operation, choice):
    first = repo.save(WordEntry('first', [Sense('n.', '')]))
    second = repo.save(WordEntry('second', [Sense('v.', '')]))
    window.reset_form(first)
    window.blocks[0].word.setText('changed')
    draft = window.form_value()

    def respond():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, UnsavedInputDialog)
            assert dialog.windowTitle() == '是否保留当前输入？'
            assert dialog.findChild(QLabel).text() == '单词编辑区域还有未保存内容'
            buttons = dialog.findChildren(QPushButton)
            assert {button.text() for button in buttons} == {'保存', '放弃'}
            assert dialog.hasFocus()
            assert all(not button.hasFocus() and not button.isDefault() for button in buttons)
            qtbot.keyClick(dialog, Qt.Key.Key_Return)
            assert dialog.isVisible() and dialog.choice is None
            if choice == 'close':
                assert dialog.close()
            elif choice == 'escape':
                qtbot.keyClick(dialog, Qt.Key.Key_Escape)
            else:
                qtbot.mouseClick(dialog.save_button if choice == 'save' else dialog.discard_button,
                                 Qt.MouseButton.LeftButton)
        finally:
            if dialog is not None and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(30, respond)
    confirmed = choice in ('save', 'discard')
    if operation == 'close':
        assert window.close() == confirmed
        assert window.isVisible() != confirmed
    elif operation == 'edit_another':
        window.list_action(second.id, 'edit')
        assert window.edit_id == (second.id if confirmed else first.id)
    else:
        assert window.resolve_dirty() == confirmed
    assert repo.get(first.id).text == ('changed' if choice == 'save' else 'first')
    if not confirmed:
        assert window.form_value() == draft and window.is_dirty()


def test_save_validation_failure_keeps_draft_and_blocks_exit(window, repo, qtbot):
    window.blocks[0].word.setText('unfinished')
    draft = window.form_value()

    def respond():
        dialog = QApplication.activeModalWidget()
        dialog.save_button.click()

    QTimer.singleShot(30, respond)
    assert not window.close()
    assert window.isVisible() and window.form_value() == draft
    assert not repo.list_entries()

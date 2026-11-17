import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.draft_reset_dialog import DraftResetDialog


@pytest.mark.parametrize('kind', ['related', 'sense'])
@pytest.mark.parametrize('choice', ['enter', 'escape', 'close', 'confirm', 'cancel', 'tab-confirm'])
def test_form_removal_uses_compact_cancel_default_and_only_changes_draft(
        window, repo, qtbot, tmp_path, kind, choice):
    entry = repo.save(WordEntry('main', [Sense('n.', 'first'), Sense('v.', 'second')],
                               [RelatedWord('child', [Sense('n.', 'child meaning')])]))
    window.reset_form(entry)
    main, child = window.blocks
    child.word.setFocus()
    qtbot.wait(20)
    before = window.form_value()
    errors = []

    def respond():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, DraftResetDialog)
            assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
            assert dialog.findChild(QLabel).text() == ('确认删除关联词' if kind == 'related' else '确认删除释义')
            assert {button.text() for button in dialog.findChildren(QPushButton)} == {'确认', '取消'}
            qtbot.waitUntil(dialog.isActiveWindow)
            assert dialog.cancel_button.hasFocus() and dialog.cancel_button.isDefault()
            assert dialog.cancel_button.width() >= 72
            dialog.grab().save(str(tmp_path / f'{kind}-confirmation.png'))
            if choice == 'tab-confirm':
                qtbot.keyClick(dialog.cancel_button, Qt.Key.Key_Tab)
                assert dialog.confirm_button.hasFocus()
                qtbot.keyClick(dialog.confirm_button, Qt.Key.Key_Return)
            elif choice in ('confirm', 'cancel'):
                qtbot.mouseClick(dialog.confirm_button if choice == 'confirm' else dialog.cancel_button,
                                 Qt.MouseButton.LeftButton)
            elif choice == 'close':
                dialog.close()
            else:
                qtbot.keyClick(dialog.cancel_button, Qt.Key.Key_Return if choice == 'enter' else Qt.Key.Key_Escape)
        except BaseException as error:
            errors.append(error)
        finally:
            if dialog is not None and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, respond)
    qtbot.mouseClick(window.remove_related_button if kind == 'related' else main.rows[1].remove_button,
                     Qt.MouseButton.LeftButton)
    assert not errors, errors
    assert repo.get(entry.id) == entry
    if choice in ('confirm', 'tab-confirm'):
        if kind == 'related':
            assert window.blocks == [main]
        else:
            assert len(main.rows) == 1 and main.rows[0].value() == entry.senses[0]
    else:
        assert window.form_value() == before

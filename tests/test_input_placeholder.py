import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from vocabulary.widgets import SenseRow


def test_pending_hint_refresh_after_viewport_teardown(qtbot):
    row = SenseRow()
    qtbot.addWidget(row)
    field = row.translation
    hint = field.enter_placeholder
    with qtbot.captureExceptions() as errors:
        # Replay the logged refresh after the QWidget viewport has died while
        # the outer text editor is still alive, independently of its contents.
        QTimer.singleShot(0, lambda: hint.refresh())
        field.setViewport(QWidget())
        qtbot.wait(10)
    assert not errors, [(kind.__name__, str(error)) for kind, error, _ in errors]


@pytest.mark.parametrize('name,description', [
    ('main', '单词（回车 换词）'), ('related', '关联词（回车 换词）'),
    ('translation', '翻译（回车 换行）'),
])
def test_enter_hint_does_not_interfere_with_input_or_composition(window, qtbot, name, description):
    related = window.add_block(related=True)
    field = {'main': window.blocks[0].word, 'related': related.word,
             'translation': window.blocks[0].rows[0].translation}[name]
    field.setFocus()
    qtbot.wait(10)
    hint = field.enter_placeholder
    assert hint.isVisible() and field.accessibleDescription() == description
    assert field.placeholderText() == ''  # Only the icon hint is painted.
    QApplication.sendEvent(field, QInputMethodEvent('preedit', []))
    assert not hint.isVisible()
    QApplication.sendEvent(field, QInputMethodEvent('', []))
    assert hint.isVisible()
    qtbot.keyClicks(field, 'hello')
    assert not hint.isVisible()
    assert (field.text() if isinstance(field, QLineEdit) else field.toPlainText()) == 'hello'
    qtbot.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(field, Qt.Key.Key_Backspace)
    assert hint.isVisible() and field.hasFocus()

"""Place newly focused inputs at the end without changing ongoing editing."""
import shiboken6
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit, QWidget


class InputFocusPolicy(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        app = QApplication.instance()
        app.focusChanged.connect(self._focus_changed)
        app.installEventFilter(self)

    def _input(self, widget):
        if not isinstance(widget, QWidget):
            return None
        editor = None
        # QWidget.isAncestorOf excludes owned top-level dialogs; follow their parent chain too.
        while widget is not None:
            if widget is self.window:
                return editor
            if editor is None:
                if isinstance(widget, QAbstractSpinBox):
                    editor = widget.lineEdit()
                elif isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit)):
                    editor = widget
            widget = widget.parentWidget()
        return None

    @staticmethod
    def _move_to_end(editor):
        if editor is None or not shiboken6.isValid(editor) or editor.isReadOnly():
            return
        if isinstance(editor, QLineEdit):
            editor.end(False)
        else:
            editor.moveCursor(QTextCursor.MoveOperation.End)
            editor.ensureCursorVisible()

    def _focus_changed(self, old, new):
        # This signal follows Qt's own focus handler, including Tab's select-all.
        self._move_to_end(self._input(new))

    def eventFilter(self, source, event):
        if event.type() == QEvent.Type.FocusIn:
            editor = self._input(source)
            if editor is not None and (event.reason() == Qt.FocusReason.MouseFocusReason
                                       or isinstance(source, QAbstractSpinBox)
                                       or isinstance(editor.parentWidget(), QAbstractSpinBox)):
                # Mouse clicks and spin boxes place/select text after delivering focus.
                QTimer.singleShot(0, lambda: self._finish_focus(editor))
        return False

    def _finish_focus(self, editor):
        if shiboken6.isValid(editor):
            focus = QApplication.focusWidget()
            if self._input(focus) is editor:
                self._move_to_end(editor)

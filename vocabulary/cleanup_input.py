"""Non-interactive command suffix hints; never completes or changes input."""
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QLineEdit

from .domain import CLEANUP_COMMANDS


class CleanupInput(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName('清空内容确认信息')
        self.setPlaceholderText('输入确认信息')
        self.textChanged.connect(self.update)
        self.cursorPositionChanged.connect(self.update)
        self.selectionChanged.connect(self.update)

    def hint_suffix(self):
        text = self.text()
        if not text or self.hasSelectedText() or self.cursorPosition() != len(text):
            return ''
        return next((command[len(text):] for command in CLEANUP_COMMANDS
                     if command.startswith(text)), '')

    def paintEvent(self, event):
        super().paintEvent(event)
        suffix = self.hint_suffix()
        if not suffix:
            return
        painter = QPainter(self)
        painter.setClipRect(self.rect().adjusted(10, 2, -10, -2))
        color = QColor(self.palette().color(QPalette.ColorRole.Text))
        color.setAlpha(95)
        painter.setPen(color)
        cursor = self.cursorRect()
        painter.drawText(cursor.x() + 1, cursor.center().y()
                         + (self.fontMetrics().ascent() - self.fontMetrics().descent()) // 2,
                         suffix)

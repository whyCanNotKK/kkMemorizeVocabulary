"""Compact confirmations with Cancel anchored to a widget or table action."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QVBoxLayout

from .widgets import button


class DraftResetDialog(QDialog):
    def __init__(self, anchor, prompt, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(prompt.rstrip("？"))
        self._rect_anchor = isinstance(anchor, QRect)
        self.anchor_rect = (QRect(anchor) if self._rect_anchor else
                            QRect(anchor.mapToGlobal(QPoint()), anchor.size()))
        button_size = QSize(72, 36) if self._rect_anchor else anchor.size()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(QLabel(prompt))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()
        self.confirm_button = button("确认")
        self.cancel_button = button("取消")
        for control in (self.confirm_button, self.cancel_button):
            control.setFixedSize(button_size)
            control.setAutoDefault(False)
            actions.addWidget(control)
        self.cancel_button.setDefault(True)
        self.confirm_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        layout.addLayout(actions)
        self.setFixedSize(self.sizeHint())
        self.cancel_button.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        self.layout().activate()
        position = self.anchor_rect.topLeft() - self.cancel_button.mapTo(self, QPoint())
        if self._rect_anchor:
            screen = QApplication.screenAt(self.anchor_rect.center()) or self.screen()
            available = screen.availableGeometry()
            position.setX(max(available.left(), min(position.x(), available.right() - self.width() + 1)))
            position.setY(max(available.top(), min(position.y(), available.bottom() - self.height() + 1)))
        # Keep Cancel over the trigger unless the table action is at a screen edge.
        self.move(position)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

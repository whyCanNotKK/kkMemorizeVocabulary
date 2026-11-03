"""Confirm discarding a draft with Cancel placed over the initiating button."""
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from .widgets import button


class DraftResetDialog(QDialog):
    def __init__(self, anchor, prompt, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(prompt.rstrip("？"))
        self.anchor_rect = QRect(anchor.mapToGlobal(QPoint()), anchor.size())
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
            control.setFixedSize(anchor.size())
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
        # Extend above/left of the anchor, leaving Cancel in exactly the same place.
        self.move(position)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

"""Ask about a draft without selecting a destructive or saving action by default."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from .widgets import button


class UnsavedInputDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('是否保留当前输入？')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.choice = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(18)
        layout.addWidget(QLabel('单词编辑区域还有未保存内容'))
        actions = QHBoxLayout()
        actions.addStretch()
        self.save_button = button('保存')
        self.discard_button = button('放弃')
        for control, choice in ((self.save_button, 'save'), (self.discard_button, 'discard')):
            control.setAutoDefault(False)
            control.setDefault(False)
            control.clicked.connect(lambda checked=False, choice=choice: self.choose(choice))
            actions.addWidget(control)
        layout.addLayout(actions)
        self.setFixedSize(self.sizeHint())
        self.setFocus()

    def choose(self, choice):
        self.choice = choice
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

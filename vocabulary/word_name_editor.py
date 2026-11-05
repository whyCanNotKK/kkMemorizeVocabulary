"""One inline word editor with a companion phonetic field over the table."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLineEdit, QVBoxLayout


class WordNameEditor(QLineEdit):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAccessibleName("单词")
        self.panel = QFrame(parent)
        self.panel.setObjectName("wordPhoneticEditor")
        self.panel.setStyleSheet(
            "QFrame#wordPhoneticEditor { background: #1e242d; border: 1px solid #536476; border-radius: 7px; }")
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(4, 4, 4, 4)
        self.phonetic = QLineEdit()
        self.phonetic.setPlaceholderText("音标（可选）")
        self.phonetic.setAccessibleName("单词音标")
        self.phonetic.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        layout.addWidget(self.phonetic)
        self.panel.hide()
        self.destroyed.connect(self.panel.deleteLater)

    def owns_focus(self, focus):
        return focus is self or focus is self.phonetic or (focus is not None and self.panel.isAncestorOf(focus))

    def place_phonetic(self):
        bounds = self.parentWidget().rect()
        width = min(bounds.width(), max(160, self.width()))
        height = 44
        x = max(0, min(self.x(), bounds.width() - width))
        y = self.geometry().bottom() + 3
        if y + height > bounds.height():
            y = max(0, self.y() - height - 3)
        self.panel.setGeometry(x, y, width, height)
        self.panel.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.place_phonetic()
        self.panel.show()

    def hideEvent(self, event):
        self.panel.hide()
        super().hideEvent(event)

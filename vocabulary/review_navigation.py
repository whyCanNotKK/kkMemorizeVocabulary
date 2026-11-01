"""Route review-area navigation without taking keys from text inputs."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox,
    QHeaderView, QLineEdit, QPlainTextEdit, QTextEdit, QWidget,
)


def navigation_step(event):
    if event.modifiers() != Qt.KeyboardModifier.NoModifier:
        return None
    return {Qt.Key.Key_W: -1, Qt.Key.Key_Up: -1,
            Qt.Key.Key_S: 1, Qt.Key.Key_Down: 1}.get(event.key())


class ReviewKeyRouter(QObject):
    """Only list-area controls may hand navigation to the active table."""

    def __init__(self, panel, active_table):
        super().__init__(panel)
        self.panel = panel
        self.active_table = active_table
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, source, event):
        if event.type() != QEvent.Type.KeyPress:
            return False
        step = navigation_step(event)
        if step is None or not isinstance(source, QWidget):
            return False
        app = QApplication.instance()
        if (source is not app.focusWidget() or not self.panel.isVisible()
                or app.activeWindow() is not self.panel.window()
                or app.activeModalWidget() or app.activePopupWidget()):
            return False
        if source is not self.panel and not self.panel.isAncestorOf(source):
            return False
        if source is not self.panel and not isinstance(source, (QAbstractButton, QAbstractSlider, QHeaderView)):
            return False
        # Input-owned buttons (e.g. search's clear button) keep their input scope.
        ancestor = source
        while ancestor is not self.panel:
            if isinstance(ancestor, (QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QAbstractSpinBox)):
                return False
            ancestor = ancestor.parentWidget()
        if self.active_table().navigate_entries(step):
            event.accept()
            return True
        return False

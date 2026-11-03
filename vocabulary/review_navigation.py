"""Route review controls and the search/list arrow-key handoff."""
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
    """Keep navigation within the list area and its search field."""

    def __init__(self, panel, active_table, search_box=None):
        super().__init__(panel)
        self.panel = panel
        self.active_table = active_table
        self.search_box = search_box
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
        table = self.active_table()
        if self.search_box is not None and self.search_box.isVisible():
            if source is self.search_box and event.key() == Qt.Key.Key_Down:
                if table.navigate_entries(1, first=True):
                    event.accept()
                    return True
            if (source is table and event.key() == Qt.Key.Key_Up
                    and self.search_box.text().strip() and table.can_navigate_entries()):
                current = table.currentIndex()
                row = current.data(Qt.ItemDataRole.UserRole)
                rows = table.model().rows
                if (rows and row is not None and table.selectionModel().isSelected(current)
                        and row.entry.id == rows[0].entry.id):
                    self.search_box.setFocus(Qt.FocusReason.OtherFocusReason)
                    event.accept()
                    return True
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

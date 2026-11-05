"""Word-table boundaries: fixed star and POS columns, adjustable content widths."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHeaderView


class WordTableHeader(QHeaderView):
    def __init__(self, table):
        super().__init__(Qt.Orientation.Horizontal, table)
        self.table = table
        self._resize_drag = None
        self.setMouseTracking(True)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(150, 161, 177, 100), 1))
        top = max(0, (self.height() - 14) // 2)
        for column in (1, 3):
            edge = self.sectionViewportPosition(column) + self.sectionSize(column)
            for offset in (-2, 2):
                painter.drawLine(edge + offset, top, edge + offset, top + 13)

    def _boundary_at(self, point):
        for column in (1, 3):
            edge = self.sectionViewportPosition(column) + self.sectionSize(column)
            if abs(point.x() - edge) <= 5:
                return column
        return None

    def _prepare_resize(self):
        if not self.table.delegate.submit():
            return False
        self.table.cancel_drag()
        return True

    def mousePressEvent(self, event):
        column = self._boundary_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and column is not None:
            event.accept()
            if not self._prepare_resize():
                return
            edge = self.sectionViewportPosition(column) + self.sectionSize(column)
            self._resize_drag = (column, event.position().x(), edge)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_drag is not None:
            column, origin, edge = self._resize_drag
            self.table.resize_boundary(column, edge + event.position().x() - origin)
            event.accept()
            return
        super().mouseMoveEvent(event)
        if self._boundary_at(event.position()) is not None:
            self.viewport().setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.viewport().unsetCursor()

    def mouseReleaseEvent(self, event):
        if self._resize_drag is not None and event.button() == Qt.MouseButton.LeftButton:
            self._resize_drag = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._resize_drag = None
        column = self._boundary_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and column is not None and self._prepare_resize():
            self.table.reset_boundary(column)
        self.viewport().unsetCursor()
        event.accept()

    def hideEvent(self, event):
        self._resize_drag = None
        self.viewport().unsetCursor()
        super().hideEvent(event)

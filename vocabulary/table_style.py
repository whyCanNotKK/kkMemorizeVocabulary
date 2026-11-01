"""Word grouping decorations shared by normal table and drag-preview painting.

The view supplies TableRow-shaped UserRole projections (owner, sense, word,
first, hidden and padding). No word state or input handling belongs here.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QStyle


class WordGroupStyle:
    POS_MARGIN = 5
    BACKGROUND = "#1e242d"
    SELECTED = "#283440"
    RAIL = "#70869c"
    SELECTED_RAIL = "#9fb9d0"
    SEPARATOR = "#3a4654"

    def __init__(self, view):
        self.view = view

    def paint(self, painter, option, index):
        """Paint only decoration, clipped to this cell at its supplied position."""
        row = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.fillRect(rect, QColor(self.SELECTED if selected else self.BACKGROUND))

        if not row.hidden and index.column() == 3:
            # Resolve a word in O(1), including senses outside the viewport.
            first = index.row() - row.sense
            last = first + len(row.word.senses) - 1
            model = index.model()
            first_row = model.index(first, 3).data(Qt.ItemDataRole.UserRole)
            last_row = model.index(last, 3).data(Qt.ItemDataRole.UserRole)
            bounds = QRectF(self.view.visualRect(model.index(first, 3))).united(
                QRectF(self.view.visualRect(model.index(last, 3))))
            # Drag previews reuse the same projection at a different location.
            offset = rect.topLeft() - QRectF(self.view.visualRect(index)).topLeft()
            bounds.translate(offset)
            answer = self.view.visualRect(model.index(first, 3))
            rail = QRectF(answer.left() + offset.x() + 5,
                          bounds.top() + first_row.top_padding, 2,
                          bounds.height() - first_row.top_padding - last_row.bottom_padding)
            painter.fillRect(rail, QColor(self.SELECTED_RAIL if selected else self.RAIL))

        if row.first:
            painter.setPen(QPen(QColor(self.SEPARATOR), 1))
            painter.drawLine(rect.topLeft(), rect.topRight())
        painter.restore()

from dataclasses import dataclass
import time

import shiboken6
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPersistentModelIndex, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QLineEdit, QPlainTextEdit,
    QStyle, QStyledItemDelegate, QTableView,
)

from .domain import WordEntry
from .theme import ACCENT, GOLD, MUTED, TEXT, draw_symbol
from .widgets import PosCombo


@dataclass
class TableRow:
    entry: WordEntry
    owner: int = -1  # -1: main word; otherwise related word index
    sense: int = 0
    hidden: bool = False

    @property
    def word(self):
        return self.entry if self.owner == -1 else self.entry.related[self.owner]

    @property
    def first(self):
        return self.owner == -1 and self.sense == 0


class WordTableModel(QAbstractTableModel):
    def __init__(self, edit_callback, parent=None):
        super().__init__(parent)
        self.rows = []
        self.edit_callback = edit_callback

    def populate(self, entries, revealed):
        self.beginResetModel()
        self.rows = []
        for entry in entries:
            if entry.id not in revealed:
                self.rows.append(TableRow(entry, hidden=True))
                continue
            for index in range(len(entry.senses)):
                self.rows.append(TableRow(entry, -1, index))
            for owner, child in enumerate(entry.related):
                for index in range(len(child.senses)):
                    self.rows.append(TableRow(entry, owner, index))
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 5

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ("", "单词", "词性", "翻译", "操作")[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row, column = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole):
            if column == 1:
                return row.word.text if row.sense == 0 else ""
            if column in (2, 3) and not row.hidden:
                sense = row.word.senses[row.sense]
                return sense.pos if column == 2 else sense.translation
        if role == Qt.ItemDataRole.AccessibleTextRole:
            if column == 0 and row.first:
                return "重点" if row.entry.important else "非重点"
            if row.hidden and column == 2:
                return "答案已隐藏，点击查看词性、翻译和关联词"
            if column == 4 and row.first:
                return ("恢复待记" if row.entry.learned else "标记已记下") + "；编辑整组；删除单词"
        return None

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            row, column = self.rows[index.row()], index.column()
            if (column == 1 and row.sense == 0) or (column in (2, 3) and not row.hidden):
                flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        return self.edit_callback(self.rows[index.row()], index.column(), value)


def text_document(text, font, width):
    document = QTextDocument()
    document.setDocumentMargin(0)
    document.setDefaultFont(font)
    option = document.defaultTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setPlainText(text)
    document.setTextWidth(max(20, width))
    return document


class WordDelegate(QStyledItemDelegate):
    editingChanged = Signal(bool)

    def __init__(self, view, before_edit):
        super().__init__(view)
        self.view = view
        self.before_edit = before_edit
        self.editor = None
        self.edit_index = None
        self._committing = False

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, QColor("#283440" if selected else "#1e242d"))
        if row.first:
            painter.setPen(QPen(QColor("#343e4a"), 1))
            painter.drawLine(rect.topLeft(), rect.topRight())
        column = index.column()
        if column == 0 and row.first:
            draw_symbol(painter, "star", QRectF(rect.center().x() - 8, rect.top() + 17, 16, 16),
                        GOLD if row.entry.important else "#596371")
        elif column == 4 and row.first:
            for action, target in self.view.action_rects(option.rect):
                name = ("minus" if row.entry.learned else "circle") if action == "learned" else action
                color = "#ef929a" if action == "learned" and row.entry.learned else (
                    ACCENT if action == "learned" else MUTED)
                draw_symbol(painter, name, QRectF(target).adjusted(7, 7, -7, -7), color)
        elif column == 2 and row.hidden:
            mask = QRectF(rect.left() + 12, rect.top() + 19, min(360, rect.width() - 28), 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#788493"))
            painter.drawRoundedRect(mask, 7, 7)
        elif column in (1, 2, 3):
            text = index.data() or ""
            font = QFont(option.font)
            if column == 1:
                font.setPixelSize(15 if row.owner == -1 else 13)
                font.setWeight(QFont.Weight.DemiBold if row.owner == -1 else QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QColor(TEXT if row.owner == -1 else "#bdc8d5"))
                left = 10
                if row.owner >= 0 and row.sense == 0:
                    painter.setPen(QPen(QColor("#7f8d9e"), 1.2))
                    x, y = int(rect.left()) + 12, int(rect.top()) + 18
                    painter.drawLine(x, y, x, y + 7)
                    painter.drawLine(x, y + 7, x + 7, y + 7)
                    painter.drawLine(x + 4, y + 4, x + 7, y + 7)
                    left = 26
                    painter.setPen(QColor("#bdc8d5"))
                metrics = painter.fontMetrics()
                text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width()) - left - 10)
                painter.drawText(rect.adjusted(left, 14, -10, -8), Qt.AlignmentFlag.AlignTop, text)
            elif column == 2:
                painter.setPen(QColor(MUTED))
                painter.drawText(rect.adjusted(10, 16, -6, -8), Qt.AlignmentFlag.AlignTop, text)
            elif not row.hidden:
                document = text_document(text, font, rect.width() - 24)
                painter.translate(rect.left() + 12, rect.top() + 14)
                context = document.documentLayout().PaintContext()
                context.palette.setColor(context.palette.ColorRole.Text, QColor(TEXT))
                document.documentLayout().draw(painter, context)
        painter.restore()

    def sizeHint(self, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row and not row.hidden and index.column() == 3:
            document = text_document(index.data() or "", option.font, self.view.columnWidth(3) - 24)
            return QSize(100, max(48, int(document.size().height()) + 28))
        return QSize(50, 52)

    def createEditor(self, parent, option, index):
        if not self.before_edit():
            return None
        if index.column() == 2:
            editor = PosCombo(parent)
        elif index.column() == 3:
            editor = QPlainTextEdit(parent)
            editor.setTabChangesFocus(True)
        else:
            editor = QLineEdit(parent)
        self.editor = editor
        self.edit_index = QPersistentModelIndex(index)
        self.editingChanged.emit(True)
        editor.destroyed.connect(lambda: self._destroyed(editor))
        return editor

    def _destroyed(self, editor):
        if self.editor is editor:
            self.editor = None
            self.edit_index = None
            self.editingChanged.emit(False)

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole) or ""
        if isinstance(editor, PosCombo):
            editor.set_pos(value)
        elif isinstance(editor, QPlainTextEdit):
            editor.setPlainText(value)
        else:
            editor.setText(value)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(3, 3, -3, -3))

    def setModelData(self, editor, model, index):
        # Our event filter controls commit/close so validation failures retain the editor.
        pass

    def _value(self, editor):
        if isinstance(editor, PosCombo):
            return editor.currentData() or ""
        if isinstance(editor, QPlainTextEdit):
            return editor.toPlainText()
        return editor.text()

    def submit(self):
        editor = self.editor
        if editor is None or not shiboken6.isValid(editor):
            return True
        if self._committing:
            return False
        self._committing = True
        try:
            index = QModelIndex(self.edit_index)
            if index.isValid() and not self.view.model().setData(index, self._value(editor)):
                QTimer.singleShot(0, editor.setFocus)
                return False
            self.editor, self.edit_index = None, None
            self.editingChanged.emit(False)
            self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
            return True
        finally:
            self._committing = False

    def cancel(self):
        editor = self.editor
        if editor and shiboken6.isValid(editor):
            self.editor, self.edit_index = None, None
            self.editingChanged.emit(False)
            self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.RevertModelCache)

    def _focus_out(self, editor):
        if self.editor is not editor or not shiboken6.isValid(editor) or self._committing:
            return
        focus = QApplication.focusWidget()
        if focus is editor or (focus and editor.isAncestorOf(focus)):
            return
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return
        self.submit()

    def eventFilter(self, editor, event):
        if editor is self.editor:
            if event.type() == QEvent.Type.KeyPress:
                key = event.key()
                if key == Qt.Key.Key_Escape:
                    if isinstance(editor, PosCombo) and editor.view().isVisible():
                        return False
                    self.cancel()
                    return True
                if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                    if self.submit():
                        self.view.setFocus()
                        self.view.focusNextPrevChild(key == Qt.Key.Key_Tab)
                    return True
                # Let plain text handle Enter/newlines and the combo handle digits/popup.
                return False
            if event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, lambda: self._focus_out(editor))
                return False
        return False


class WordTable(QTableView):
    revealRequested = Signal(int, bool)
    actionRequested = Signal(int, str)

    def __init__(self, model, before_edit, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.delegate = WordDelegate(self, before_edit)
        self.setItemDelegate(self.delegate)
        self.setShowGrid(False)
        self.setWordWrap(True)
        self.setMouseTracking(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.setTabKeyNavigation(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(34)
        for column, width in ((0, 38), (1, 206), (2, 78), (4, 120)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._just_revealed = (None, 0)

    def update_spans(self):
        self.clearSpans()
        for i, row in enumerate(self.model().rows):
            if row.hidden:
                self.setSpan(i, 2, 1, 2)
        self.resizeRowsToContents()

    @staticmethod
    def action_rects(rect):
        from PySide6.QtCore import QRect
        return [(name, QRect(rect.left() + 5 + i * 34, rect.top() + 9, 32, 32))
                for i, name in enumerate(("learned", "edit", "trash"))]

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            row = index.data(Qt.ItemDataRole.UserRole)
            column = index.column()
            if self.delegate.editor:
                if not self.delegate.submit():
                    event.accept()
                    return
            if column == 0 and row.first:
                self.actionRequested.emit(row.entry.id, "important")
                event.accept()
                return
            if column == 4 and row.first:
                for action, rect in self.action_rects(self.visualRect(index)):
                    if rect.contains(event.position().toPoint()):
                        self.actionRequested.emit(row.entry.id, action)
                        event.accept()
                        return
            if row.hidden and column in (2, 3):
                self._just_revealed = (row.entry.id, time.monotonic())
                self.revealRequested.emit(row.entry.id, True)
                event.accept()
                return
            if column == 3 and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.revealRequested.emit(row.entry.id, False)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            row = index.data(Qt.ItemDataRole.UserRole)
            if row.entry.id == self._just_revealed[0] and time.monotonic() - self._just_revealed[1] < 0.5:
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                event.accept()
                return
            if self.model().flags(index) & Qt.ItemFlag.ItemIsEditable:
                target = (row.entry.id, row.owner, row.sense, index.column())
                if not self.delegate.before_edit():
                    event.accept()
                    return
                # Saving the bottom draft may have rebuilt/reordered the projection.
                matching = next((i for i, item in enumerate(self.model().rows)
                                 if (item.entry.id, item.owner, item.sense) == target[:3]), None)
                if matching is None:
                    event.accept()
                    return
                index = self.model().index(matching, target[3])
                self.setCurrentIndex(index)
                self.edit(index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            from PySide6.QtWidgets import QToolTip
            index = self.indexAt(event.pos())
            if index.isValid():
                row = index.data(Qt.ItemDataRole.UserRole)
                text = ""
                if index.column() == 4 and row.first:
                    labels = {"learned": "恢复待记" if row.entry.learned else "标记已记下",
                              "edit": "编辑整组", "trash": "删除单词"}
                    for name, rect in self.action_rects(self.visualRect(index)):
                        if rect.contains(event.pos()):
                            text = labels[name]
                elif index.column() == 0 and row.first:
                    text = "取消重点" if row.entry.important else "设为重点"
                elif row.hidden and index.column() in (2, 3):
                    text = "点击查看词性、翻译和关联词"
                else:
                    text = index.data(Qt.ItemDataRole.ToolTipRole) or ""
                if text:
                    QToolTip.showText(event.globalPos(), text, self)
                    return True
        return super().viewportEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.resizeRowsToContents)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.model().rows:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(MUTED))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                             "这里还没有单词\n在下方添加，开始积累你的单词本")

from dataclasses import dataclass
import math
import time

import shiboken6
from PySide6.QtCore import QAbstractTableModel, QEasingCurve, QEvent, QItemSelection, QItemSelectionModel, QModelIndex, QPoint, QPersistentModelIndex, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QLineEdit, QPlainTextEdit,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView,
)

from .domain import POS_OPTIONS, WordEntry
from .theme import ACCENT, GOLD, MUTED, TEXT, draw_related_arrow, draw_symbol
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

    @property
    def top_padding(self):
        return 7 if self.first else (3 if self.sense == 0 else 1)

    @property
    def minimum_height(self):
        return 36 if self.first else (26 if self.sense == 0 else 22)


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
        top = row.top_padding
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, QColor("#283440" if selected else "#1e242d"))
        if row.first:
            painter.setPen(QPen(QColor("#343e4a"), 1))
            painter.drawLine(rect.topLeft(), rect.topRight())
        column = index.column()
        if column == 0 and row.first:
            draw_symbol(painter, "star", QRectF(rect.center().x() - 8, rect.top() + 10, 16, 16),
                        GOLD if row.entry.important else "#596371")
        elif column == 4 and row.first:
            for action, target in self.view.action_rects(option.rect):
                if action == "drag" and not self.view.reorder_enabled:
                    color = "#46505c"
                else:
                    color = "#ef929a" if action == "learned" and row.entry.learned else (
                        ACCENT if action == "learned" else MUTED)
                name = ("minus" if row.entry.learned else "circle") if action == "learned" else action
                draw_symbol(painter, name, QRectF(target).adjusted(7, 7, -7, -7), color)
        elif column == 2 and row.hidden:
            mask = QRectF(rect.left() + 12, rect.top() + 11, min(360, rect.width() - 28), 14)
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
                    x, y = int(rect.left()) + 12, int(rect.top()) + top + 3
                    draw_related_arrow(painter, x, y)
                    left = 26
                    painter.setPen(QColor("#bdc8d5"))
                metrics = painter.fontMetrics()
                text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width()) - left - 10)
                painter.drawText(rect.adjusted(left, top, -10, -top), Qt.AlignmentFlag.AlignTop, text)
            elif column == 2:
                badge = QRectF(rect.left() + (rect.width() - self.view.pos_badge_width) / 2,
                               rect.top() + top, self.view.pos_badge_width, 20)
                painter.setPen(QPen(QColor("#465463"), 1))
                painter.setBrush(QColor("#293440"))
                painter.drawRoundedRect(badge.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                painter.setFont(font)
                painter.setPen(QColor("#bdc8d5"))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
            elif not row.hidden:
                document = text_document(text, font, rect.width() - 24)
                painter.translate(rect.left() + 12, rect.top() + top)
                context = document.documentLayout().PaintContext()
                context.palette.setColor(context.palette.ColorRole.Text, QColor(TEXT))
                document.documentLayout().draw(painter, context)
        painter.restore()

    def sizeHint(self, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row and not row.hidden and index.column() == 3:
            document = text_document(index.data() or "", option.font, self.view.columnWidth(3) - 24)
            return QSize(100, max(row.minimum_height,
                                  math.ceil(document.size().height()) + 2 * row.top_padding))
        return QSize(50, row.minimum_height if row else 36)

    def createEditor(self, parent, option, index):
        if not self.before_edit():
            return None
        if index.column() == 2:
            editor = PosCombo(parent)
            editor.setMinimumWidth(0)
            editor.setMaximumWidth(16777215)
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
        rect = option.rect.adjusted(3, 0, -3, 0)
        rect.setHeight(max(36, rect.height()))
        if rect.bottom() > self.view.viewport().height():
            rect.moveBottom(self.view.viewport().height())
        editor.setGeometry(rect)

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
    moveRequested = Signal(int, int)
    shuffleRequested = Signal()

    def __init__(self, model, before_edit, parent=None):
        super().__init__(parent)
        self._selecting_group = False
        self.show_drag_handle = True
        self.reorder_enabled = True
        self.search_active = False
        self._drag_candidate = None
        self._drag_active = False
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
        # Hidden header section hints still constrain resizeRowsToContents().
        self.verticalHeader().setStyleSheet("QHeaderView::section { padding: 0; border: none; }")
        self.verticalHeader().setMinimumSectionSize(18)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(34)
        self.pos_badge_width = max(self.fontMetrics().horizontalAdvance(pos) for pos, _ in POS_OPTIONS) + 12
        for column, width in ((0, 38), (1, 206), (2, self.pos_badge_width + 10), (4, 120)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._just_revealed = (None, 0)
        self.shuffle_button = QPushButton(header)
        from .theme import icon
        self.shuffle_button.setIcon(icon("shuffle"))
        self.shuffle_button.setFixedSize(30, 30)
        self.shuffle_button.setObjectName("quiet")
        self.shuffle_button.setToolTip("随机排列当前待记单词；搜索时不可用")
        self.shuffle_button.setAccessibleName("随机排列待记单词")
        self.shuffle_button.clicked.connect(self.shuffleRequested)
        self._layout_shuffle_button()
        header.geometriesChanged.connect(self._layout_shuffle_button)
        self._drag_animation = QVariantAnimation(self)
        self._drag_animation.setDuration(150)
        self._drag_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._drag_animation.setStartValue(0.0)
        self._drag_animation.setEndValue(1.0)
        self._drag_animation.valueChanged.connect(self._animate_drag)
        self._drag_scroll = QTimer(self)
        self._drag_scroll.setInterval(16)
        self._drag_scroll.timeout.connect(self._auto_scroll_drag)
        self.model().modelAboutToBeReset.connect(self.cancel_drag)

    def _layout_shuffle_button(self):
        self.shuffle_button.move(4, max(0, (self.horizontalHeader().height() - 30) // 2))

    def set_page_state(self, page, searching, count):
        self.show_drag_handle = page != "all"
        self.reorder_enabled = self.show_drag_handle and not searching
        self.search_active = searching
        self.setColumnWidth(4, 154 if self.show_drag_handle else 120)
        self.shuffle_button.setVisible(page == "pending")
        self.shuffle_button.setEnabled(not searching and count >= 2)
        self.viewport().update()

    def selectionChanged(self, selected, deselected):
        super().selectionChanged(selected, deselected)
        if self._selecting_group:
            return
        indexes = self.selectionModel().selectedIndexes()
        if not indexes:
            return
        row = indexes[0].data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        rows = [i for i, item in enumerate(self.model().rows) if item.entry.id == row.entry.id]
        selection = QItemSelection(self.model().index(rows[0], 0),
                                   self.model().index(rows[-1], self.model().columnCount() - 1))
        self._selecting_group = True
        try:
            self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self._selecting_group = False

    def update_spans(self):
        self.clearSpans()
        for i, row in enumerate(self.model().rows):
            if row.hidden:
                self.setSpan(i, 2, 1, 2)
        self.resizeRowsToContents()

    def action_rects(self, rect):
        names = ("drag", "learned", "edit", "trash") if self.show_drag_handle else ("learned", "edit", "trash")
        return [(name, QRect(rect.left() + 5 + i * 34, rect.top() + 2, 32, 32))
                for i, name in enumerate(names)]

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
                        if action == "drag":
                            if self.reorder_enabled:
                                self.setCurrentIndex(index)
                                self.setFocus()
                                self._drag_candidate = (row.entry.id, event.position().toPoint())
                                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
                            event.accept()
                            return
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

    def _begin_drag(self, point):
        entry_id, origin = self._drag_candidate
        self._drag_groups = []
        for number, row in enumerate(self.model().rows):
            if row.first:
                self._drag_groups.append([row.entry.id, number, number, 0])
            self._drag_groups[-1][2] = number
            self._drag_groups[-1][3] += self.rowHeight(number)
        self._drag_source = next(i for i, group in enumerate(self._drag_groups) if group[0] == entry_id)
        self._drag_target = self._drag_source
        top = self.rowViewportPosition(self._drag_groups[self._drag_source][1])
        self._drag_grab_offset = origin.y() - top
        self._drag_point = point
        offset = 0
        self._drag_positions = {}
        for word_id, start, end, height in self._drag_groups:
            self._drag_positions[word_id] = float(offset)
            offset += height
        self._drag_active = True
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self._drag_scroll.start()
        self._update_drag_target()

    def mouseMoveEvent(self, event):
        if self._drag_candidate:
            point = event.position().toPoint()
            if not self._drag_active:
                if (point - self._drag_candidate[1]).manhattanLength() >= QApplication.startDragDistance():
                    self._begin_drag(point)
            if self._drag_active:
                self._drag_point = point
                self._update_drag_target()
                self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _update_drag_target(self):
        # Compare the dragged group's top edge with the centres of the remaining groups.
        top = self._drag_point.y() - self._drag_grab_offset + self.verticalScrollBar().value()
        others = [group for i, group in enumerate(self._drag_groups) if i != self._drag_source]
        target, offset = 0, 0
        for group in others:
            if top < offset + group[3] / 2:
                break
            target += 1
            offset += group[3]
        if target == self._drag_target:
            return
        self._drag_target = target
        reordered = list(others)
        reordered.insert(target, self._drag_groups[self._drag_source])
        self._drag_animation.stop()
        self._drag_starts = dict(self._drag_positions)
        self._drag_ends = {}
        offset = 0
        for word_id, start, end, height in reordered:
            self._drag_ends[word_id] = float(offset)
            offset += height
        self._drag_animation.start()

    def _animate_drag(self, progress):
        if not self._drag_active:
            return
        self._drag_positions = {key: start + (self._drag_ends[key] - start) * progress
                                for key, start in self._drag_starts.items()}
        self.viewport().update()

    def _auto_scroll_drag(self):
        if not self._drag_active or not self.viewport().rect().contains(self._drag_point):
            return
        y, height = self._drag_point.y(), self.viewport().height()
        delta = -max(2, (32 - y) // 2) if y < 32 else (max(2, (y - height + 32) // 2) if y > height - 32 else 0)
        if delta:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + delta)
            self._update_drag_target()
            self.viewport().update()

    def cancel_drag(self):
        self._drag_candidate = None
        self._drag_active = False
        self._drag_scroll.stop()
        self._drag_animation.stop()
        self.viewport().unsetCursor()
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if self._drag_candidate and event.button() == Qt.MouseButton.LeftButton:
            accepted = self._drag_active and self.viewport().rect().contains(event.position().toPoint())
            entry_id = self._drag_candidate[0]
            target = self._drag_target if self._drag_active else None
            changed = accepted and target != self._drag_source
            self.cancel_drag()
            if changed:
                self.moveRequested.emit(entry_id, target)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._drag_candidate and event.key() == Qt.Key.Key_Escape:
            self.cancel_drag()
            event.accept()
            return
        if self._drag_active:
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.cancel_drag()
        super().focusOutEvent(event)

    def _paint_drag_group(self, painter, group, top):
        word_id, start, end, height = group
        selected = self.currentIndex().data(Qt.ItemDataRole.UserRole)
        for row in range(start, end + 1):
            row_height = self.rowHeight(row)
            if top + row_height >= 0 and top <= self.viewport().height():
                item = self.model().rows[row]
                for column in range(self.model().columnCount()):
                    if item.hidden and column == 3:
                        continue
                    option = QStyleOptionViewItem()
                    self.initViewItemOption(option)
                    option.rect = QRect(self.columnViewportPosition(column), round(top),
                                        self.columnWidth(column) + (self.columnWidth(3) if item.hidden and column == 2 else 0),
                                        row_height)
                    if selected and selected.entry.id == word_id:
                        option.state |= QStyle.StateFlag.State_Selected
                    self.delegate.paint(painter, option, self.model().index(row, column))
            top += row_height

    def _paint_drag(self):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor("#1e242d"))
        scroll = self.verticalScrollBar().value()
        for i, group in enumerate(self._drag_groups):
            if i != self._drag_source:
                self._paint_drag_group(painter, group, self._drag_positions[group[0]] - scroll)
        source = self._drag_groups[self._drag_source]
        top = self._drag_point.y() - self._drag_grab_offset
        self._paint_drag_group(painter, source, top)
        painter.setPen(QPen(QColor(ACCENT), 1.5))
        painter.drawRect(QRectF(1, top + 1, self.viewport().width() - 2, source[3] - 2))

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
                              "edit": "编辑整组", "trash": "删除单词",
                              "drag": "按住拖动整个词条" if self.reorder_enabled else "搜索时不可调整排序"}
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
        if self._drag_active:
            self._paint_drag()
            return
        super().paintEvent(event)
        if not self.model().rows:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(MUTED))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                             "未找到匹配单词" if self.search_active else
                             "这里还没有单词\n在下方添加，开始积累你的单词本")

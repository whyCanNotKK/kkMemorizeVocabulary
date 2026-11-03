from dataclasses import dataclass
from contextlib import contextmanager
import math
import time

import shiboken6
from PySide6.QtCore import QAbstractTableModel, QEasingCurve, QEvent, QItemSelection, QItemSelectionModel, QModelIndex, QPoint, QPointF, QPersistentModelIndex, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSlider, QApplication, QHeaderView, QLineEdit, QPlainTextEdit,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView,
)

from .domain import POS_OPTIONS, WordEntry
from .drag_reorder import ANIMATION_MS, edge_scroll_delta, group_positions, insertion_index
from .review_navigation import navigation_step
from .table_style import WordGroupStyle
from .theme import ACCENT, GOLD, MUTED, TEXT, draw_related_arrow, draw_symbol
from .widgets import ExpandingTextEdit
from .pos_picker import PosPicker


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
        return 7 if self.first else (11 if self.sense == 0 else 0)

    @property
    def bottom_padding(self):
        last = (self.owner == len(self.entry.related) - 1
                and self.sense == len(self.word.senses) - 1)
        return 9 if last and not self.hidden else 1

    @property
    def minimum_height(self):
        # Extra bottom space belongs only to the complete entry group's last row.
        return 36 if self.hidden else self.top_padding + 20 + self.bottom_padding


class WordTableModel(QAbstractTableModel):
    def __init__(self, edit_callback, parent=None):
        super().__init__(parent)
        self.rows = []
        self.edit_callback = edit_callback
        self.study_groups = None

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
        if self.study_groups is not None and row.first:
            if column == 0 and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole,
                                       Qt.ItemDataRole.AccessibleTextRole):
                return str(self.study_groups[row.entry.id])
            if column == 4 and role == Qt.ItemDataRole.AccessibleTextRole:
                return "记下"
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.ToolTipRole and column in (1, 2, 3):
            return None
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
                return "答案已隐藏，点击灰色遮罩查看词性、翻译和关联词"
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
        if role != Qt.ItemDataRole.EditRole or not self.flags(index) & Qt.ItemFlag.ItemIsEditable:
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
        self.word_style = WordGroupStyle(view)
        self.before_edit = before_edit
        self.editor = None
        self.edit_index = None
        self._committing = False

    @staticmethod
    def mask_rect(rect):
        rect = QRectF(rect)
        return QRectF(rect.left() + 12, rect.top() + 11, min(360, rect.width() - 28), 14)

    @classmethod
    def mask_path(cls, rect):
        path = QPainterPath()
        path.addRoundedRect(cls.mask_rect(rect), 7, 7)
        return path

    def mask_hit(self, index, point):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None or not row.hidden or index.column() not in (2, 3):
            return False
        cell = QRectF(self.view.visualRect(index))
        mask = self.mask_rect(cell)
        return QRectF(mask.left(), cell.top(), mask.width(), cell.height()).contains(point)

    def word_layout(self, index, rect, base_font):
        """Use the same word geometry for painting and pointer hit testing."""
        row = index.data(Qt.ItemDataRole.UserRole)
        font = QFont(base_font)
        font.setPixelSize(15 if row.owner == -1 else 13)
        font.setWeight(QFont.Weight.DemiBold if row.owner == -1 else QFont.Weight.Normal)
        left = 26 if row.owner >= 0 else 10
        area = QRectF(rect).adjusted(left, row.top_padding, -10, -row.bottom_padding)
        metrics = QFontMetricsF(font)
        text = metrics.elidedText(index.data() or "", Qt.TextElideMode.ElideRight, area.width())
        return font, text, area

    def word_hit(self, index, point):
        row = index.data(Qt.ItemDataRole.UserRole)
        if index.column() != 1 or row is None or row.sense != 0:
            return False
        rect = self.view.visualRect(index)
        font, text, area = self.word_layout(index, rect, self.view.font())
        bounds = QFontMetricsF(font).boundingRect(area, Qt.AlignmentFlag.AlignTop, text)
        return bool(text) and bounds.intersected(area).intersected(QRectF(rect)).contains(point)

    def pos_rect(self, index, rect):
        row = index.data(Qt.ItemDataRole.UserRole)
        return QRectF(rect.left() + self.word_style.POS_MARGIN,
                      rect.top() + row.top_padding, self.view.pos_badge_width, 20)

    def copy_hit(self, index, point):
        if self.word_hit(index, point):
            return True
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None or row.hidden or index.column() != 3 or not index.data():
            return False
        rect = self.view.visualRect(index)
        if not rect.contains(point.toPoint() if isinstance(point, QPointF) else point):
            return False
        document = text_document(index.data(), self.view.font(), rect.width() - 24)
        local = QPointF(point) - QPointF(rect.left() + 12, rect.top() + row.top_padding)
        return document.documentLayout().hitTest(local, Qt.HitTestAccuracy.ExactHit) >= 0

    def pos_hit(self, index, point):
        row = index.data(Qt.ItemDataRole.UserRole)
        return (row is not None and not row.hidden and index.column() == 2
                and self.pos_rect(index, self.view.visualRect(index)).contains(point))

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        top = row.top_padding
        self.word_style.paint(painter, option, index)
        column = index.column()
        if column == 0 and row.first:
            if self.view.study_mode:
                painter.setPen(QColor(MUTED))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, index.data() or "")
            else:
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
                if action == "study":
                    name, color = "circle", ACCENT
                center = QRectF(target).center()
                draw_symbol(painter, name, QRectF(center.x() - 9, center.y() - 9, 18, 18), color)
        elif column == 2 and row.hidden:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#788493"))
            painter.drawPath(self.mask_path(rect))
        elif column in (1, 2, 3):
            text = index.data() or ""
            font = QFont(option.font)
            if column == 1:
                font, text, area = self.word_layout(index, rect, option.font)
                painter.setFont(font)
                painter.setPen(QColor(TEXT if row.owner == -1 else "#bdc8d5"))
                if row.owner >= 0 and row.sense == 0:
                    x, y = int(rect.left()) + 12, int(rect.top()) + top + 3
                    draw_related_arrow(painter, x, y)
                    painter.setPen(QColor("#bdc8d5"))
                painter.drawText(area, Qt.AlignmentFlag.AlignTop, text)
            elif column == 2:
                badge = self.pos_rect(index, rect)
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
        if (index.column() == 3 and isinstance(self.editor, ExpandingTextEdit)
                and self.edit_index == index):
            return QSize(100, min(self.editor.content_height, max(36, self.view.viewport().height())))
        if row and not row.hidden and index.column() == 3:
            document = text_document(index.data() or "", option.font, self.view.columnWidth(3) - 24)
            return QSize(100, max(row.minimum_height,
                                  math.ceil(document.size().height()) + row.top_padding + row.bottom_padding))
        return QSize(50, row.minimum_height if row else 36)

    def createEditor(self, parent, option, index):
        if not self.before_edit():
            return None
        if index.column() == 2:
            editor = PosPicker(parent)
            editor.view().installEventFilter(self)
            editor.chosen.connect(lambda: self._choose_pos(editor))
            editor.cancelled.connect(lambda: self._cancel_pos(editor))
        elif index.column() == 3:
            editor = ExpandingTextEdit(parent)
            editor.contentHeightChanged.connect(lambda height: self._resize_text_editor(editor))
        else:
            editor = QLineEdit(parent)
        editor.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        if isinstance(editor, QPlainTextEdit):
            editor.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
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

    def _show_pos(self, editor):
        if self.editor is editor and shiboken6.isValid(editor):
            editor.showPopup()

    def _choose_pos(self, editor):
        if self.editor is editor and self.submit():
            self.view.setFocus()

    def _cancel_pos(self, editor):
        if self.editor is editor:
            self.cancel()
            self.view.setFocus()

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole) or ""
        if isinstance(editor, PosPicker):
            editor.set_pos(value)
            QTimer.singleShot(0, lambda: self._show_pos(editor))
        elif isinstance(editor, QPlainTextEdit):
            editor.setPlainText(value)
        else:
            editor.setText(value)

    def updateEditorGeometry(self, editor, option, index):
        if isinstance(editor, PosPicker):
            rect = self.pos_rect(index, option.rect).toRect()
            editor.setGeometry(rect)
            editor.set_anchor(QRect(self.view.viewport().mapToGlobal(rect.topLeft()), rect.size()))
            return
        rect = option.rect.adjusted(3, 0, -3, 0)
        rect.setHeight(max(36, rect.height()))
        if rect.bottom() > self.view.viewport().height():
            rect.moveBottom(self.view.viewport().height())
        editor.setGeometry(rect)

    def _resize_text_editor(self, editor):
        if editor is not self.editor or self.edit_index is None or not self.edit_index.isValid():
            return
        index = QModelIndex(self.edit_index)
        self.view.resizeRowToContents(index.row())
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
        option = QStyleOptionViewItem()
        option.rect = self.view.visualRect(index)
        self.updateEditorGeometry(editor, option, index)
        editor.ensureCursorVisible()

    def setModelData(self, editor, model, index):
        # Our event filter controls commit/close so validation failures retain the editor.
        pass

    def _value(self, editor):
        if isinstance(editor, PosPicker):
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
                if isinstance(editor, PosPicker):
                    QTimer.singleShot(0, lambda: self._show_pos(editor))
                else:
                    QTimer.singleShot(0, editor.setFocus)
                return False
            if isinstance(editor, PosPicker):
                editor.hidePopup()
            self.editor, self.edit_index = None, None
            if index.isValid():
                self.view.resizeRowToContents(index.row())
            self.editingChanged.emit(False)
            self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
            return True
        finally:
            self._committing = False

    def cancel(self):
        editor = self.editor
        if editor and shiboken6.isValid(editor):
            if isinstance(editor, PosPicker):
                editor.hidePopup()
            index = QModelIndex(self.edit_index)
            self.editor, self.edit_index = None, None
            if index.isValid():
                self.view.resizeRowToContents(index.row())
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
        if (isinstance(self.editor, PosPicker) and editor is self.editor.view()
                and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)):
            backwards = event.key() == Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.editor.hidePopup()
            self.view.move_editor(-1 if backwards else 1)
            return True
        if editor is self.editor:
            if event.type() == QEvent.Type.KeyPress:
                key = event.key()
                if key == Qt.Key.Key_Escape:
                    if isinstance(editor, PosPicker) and editor.view().isVisible():
                        return False
                    self.cancel()
                    return True
                if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                    backwards = key == Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    self.view.move_editor(-1 if backwards else 1)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
                    if modifiers == Qt.KeyboardModifier.NoModifier:
                        if not event.isAutoRepeat() and self.submit():
                            self.view.setFocus()
                        return True
                    if modifiers == Qt.KeyboardModifier.ShiftModifier and isinstance(editor, QPlainTextEdit):
                        editor.insertPlainText("\n")
                        return True
                # Keep configured shortcuts and the combo's digit/popup handling.
                return False
            if event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, lambda: self._focus_out(editor))
                return False
        return False


class WordTable(QTableView):
    editPreparationRequested = Signal(int)
    copyRequested = Signal(str)
    revealRequested = Signal(int, bool)
    actionRequested = Signal(int, str)
    moveRequested = Signal(int, int)
    relatedMoveRequested = Signal(int, int, int)
    senseMoveRequested = Signal(int, int, int, int)
    shuffleRequested = Signal()

    def __init__(self, model, before_edit, parent=None):
        super().__init__(parent)
        self._selecting_group = False
        self.show_drag_handle = True
        self.reorder_enabled = True
        self.search_active = False
        self.study_mode = False
        self.empty_text = None
        self._drag_candidate = None
        self._related_drag = None
        self._sense_drag = None
        self._drag_active = False
        self._copy_press = None
        self._pending_copy = QPersistentModelIndex()
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._copy_timer.timeout.connect(self._emit_pending_copy)
        self.setModel(model)
        self.delegate = WordDelegate(self, before_edit)
        self.setItemDelegate(self.delegate)
        self.setShowGrid(False)
        self.setWordWrap(True)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
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
        for column, width in ((0, 38), (1, 206),
                              (2, self.pos_badge_width + 2 * WordGroupStyle.POS_MARGIN),
                              (4, 120)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._just_revealed = (None, 0)
        self.shuffle_button = QPushButton(header)
        from .theme import icon
        self.shuffle_button.setIcon(icon("shuffle"))
        self.shuffle_button.setFixedSize(30, 30)
        self.shuffle_button.setObjectName("quiet")
        self.shuffle_button.setToolTip("随机排列当前待记单词")
        self.shuffle_button.setAccessibleName("随机排列待记单词")
        self.shuffle_button.clicked.connect(self.shuffleRequested)
        self._layout_shuffle_button()
        header.geometriesChanged.connect(self._layout_shuffle_button)
        self._drag_animation = QVariantAnimation(self)
        self._drag_animation.setDuration(ANIMATION_MS)
        self._drag_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._drag_animation.setStartValue(0.0)
        self._drag_animation.setEndValue(1.0)
        self._drag_animation.valueChanged.connect(self._animate_drag)
        self._drag_scroll = QTimer(self)
        self._drag_scroll.setInterval(16)
        self._drag_scroll.timeout.connect(self._auto_scroll_drag)
        self.model().modelAboutToBeReset.connect(self.cancel_drag)
        self.model().modelAboutToBeReset.connect(self._cancel_pending_copy)

    def _cancel_pending_copy(self):
        self._copy_timer.stop()
        self._pending_copy = QPersistentModelIndex()
        self._copy_press = None

    def _emit_pending_copy(self):
        index = self._pending_copy
        self._pending_copy = QPersistentModelIndex()
        if index.isValid() and self.isVisible() and self.delegate.editor is None:
            self.copyRequested.emit(index.data())

    def hideEvent(self, event):
        self._cancel_pending_copy()
        self.cancel_drag()
        super().hideEvent(event)

    def _layout_shuffle_button(self):
        self.shuffle_button.move(4, max(0, (self.horizontalHeader().height() - 30) // 2))

    def set_page_state(self, page, searching, count):
        self.study_mode = page == "study17"
        self.show_drag_handle = page not in {"all", "study17"}
        self.reorder_enabled = self.show_drag_handle and not searching
        self.search_active = searching
        self.setColumnWidth(4, 54 if self.study_mode else (154 if self.show_drag_handle else 120))
        self.shuffle_button.setVisible(page == "pending" and not searching)
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

    def _entry_index(self, entry_id, owner=-1, sense=0, column=1):
        matching = [i for i, row in enumerate(self.model().rows) if row.entry.id == entry_id]
        if not matching:
            return QModelIndex()
        number = next((i for i in matching if
                       (self.model().rows[i].owner, self.model().rows[i].sense) == (owner, sense)), matching[0])
        return self.model().index(number, column)

    @contextmanager
    def preserve_view(self, *, scroll_position=None):
        """Rebuild rows while retaining logical selection and pixel scroll position."""
        current = self.currentIndex()
        row = current.data(Qt.ItemDataRole.UserRole)
        part = (row.entry.id, row.owner, row.sense, current.column()) if row else None
        selected = self.selectionModel().isSelected(current)
        position = self.verticalScrollBar().value() if scroll_position is None else scroll_position
        auto_scroll, updates = self.hasAutoScroll(), self.updatesEnabled()
        self.setAutoScroll(False)
        self.setUpdatesEnabled(False)
        try:
            yield
            self.setCurrentIndex(self._entry_index(*part) if part else QModelIndex())
            if not selected:
                self.clearSelection()
            self.doItemsLayout()
            self.verticalScrollBar().setValue(position)
        finally:
            self.setAutoScroll(auto_scroll)
            self.setUpdatesEnabled(updates)

    def select_entry(self, entry_id, *, scroll=False):
        """Select a whole entry via its main row, including after a model reset."""
        index = self._entry_index(entry_id)
        if not index.isValid():
            return
        position = self.verticalScrollBar().value()
        auto_scroll = self.hasAutoScroll()
        self.setAutoScroll(False)
        try:
            self.setCurrentIndex(index)
            self.verticalScrollBar().setValue(position)
        finally:
            self.setAutoScroll(auto_scroll)
        if scroll:
            self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def set_entry_revealed(self, entry_id, expanded):
        """The page owns reveal state; restore selection after its projection resets."""
        if self.search_active:
            return
        self.revealRequested.emit(entry_id, expanded)
        self.select_entry(entry_id)

    def edit_field(self, target):
        """Resolve a logical field again after draft handling or a saved-data refresh."""
        if not self.delegate.before_edit():
            return
        self.editPreparationRequested.emit(target[0])
        entry_id, owner, sense, column = target
        number = next((i for i, row in enumerate(self.model().rows)
                       if (row.entry.id, row.owner, row.sense) == target[:3]), None)
        if number is None:
            self.setFocus()
            return
        index = self.model().index(number, column)
        if self.model().flags(index) & Qt.ItemFlag.ItemIsEditable:
            self.setCurrentIndex(index)
            self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
            self.edit(index)

    def move_editor(self, step):
        index = QModelIndex(self.delegate.edit_index)
        if not index.isValid():
            return
        current = index.data(Qt.ItemDataRole.UserRole)
        target = (current.entry.id, current.owner, current.sense, index.column())
        fields = []
        for row in self.model().rows:
            if not row.first:
                continue
            for owner, word in [(-1, row.entry), *enumerate(row.entry.related)]:
                fields.append((row.entry.id, owner, 0, 1))
                for sense in range(len(word.senses)):
                    fields.extend((row.entry.id, owner, sense, column) for column in (2, 3))
        position = fields.index(target) + step
        if not self.delegate.submit():
            return
        if 0 <= position < len(fields):
            self.edit_field(fields[position])
        else:
            self.setFocus()

    def can_navigate_entries(self):
        return (self.isVisible() and self.isEnabled() and self.delegate.editor is None
                and self.state() != QAbstractItemView.State.EditingState and not self._drag_candidate)

    @property
    def dragging(self):
        return self._drag_candidate is not None

    def navigate_entries(self, step, *, first=False):
        """One navigation entry point for table keys and surrounding controls."""
        if not self.can_navigate_entries():
            return False
        # Choose before focusing: Qt's default focus handling may create a current cell.
        if first:
            if not self.model().rows:
                return False
            self.select_entry(self.model().rows[0].entry.id, scroll=True)
        else:
            self._move_entry_selection(step)
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        return True

    def _move_entry_selection(self, step):
        rows = self.model().rows
        if not rows:
            return
        current = self.currentIndex()
        row = current.data(Qt.ItemDataRole.UserRole)
        if row is None or not self.selectionModel().isSelected(current):
            viewport = self.viewport().rect()
            visible = next((item.entry.id for i, item in enumerate(rows)
                            if item.first and self.visualRect(self.model().index(i, 1)).intersects(viewport)), None)
            if visible is not None:
                self.select_entry(visible)
            else:
                top = self.rowAt(0)
                if top >= 0:
                    self.select_entry(rows[top].entry.id)
                else:
                    self.scrollToTop()
                    self.select_entry(rows[0].entry.id)
            return
        entries = [item.entry.id for item in rows if item.first]
        target = max(0, min(len(entries) - 1, entries.index(row.entry.id) + step))
        self.select_entry(entries[target], scroll=True)

    def action_rects(self, rect):
        names = ("study",) if self.study_mode else (
            ("drag", "learned", "edit", "trash") if self.show_drag_handle else ("learned", "edit", "trash"))
        return [(name, QRect(rect.left() + 5 + i * 34, rect.top() + 2, 32, min(32, rect.height() - 4)))
                for i, name in enumerate(names)]

    def mousePressEvent(self, event):
        self._copy_press = None
        index = self.indexAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel_pending_copy()
            self.cancel_drag()
        if (event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)
                and self._reveal_mask(index, event.position())):
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if index.isValid() and not self.related_handle_hit(index, event.position().toPoint()):
                self._edit_pointer_field(index)
            event.accept()
            return
        if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            row = index.data(Qt.ItemDataRole.UserRole)
            column = index.column()
            related_hit = self.related_handle_hit(index, event.position().toPoint())
            sense_hit = self.delegate.pos_hit(index, event.position())
            if self.delegate.editor:
                if not self.delegate.submit():
                    event.accept()
                    return
                if related_hit or sense_hit:
                    self.editPreparationRequested.emit(row.entry.id)
                    matching = next((i for i, item in enumerate(self.model().rows)
                                     if (item.entry.id, item.owner, item.sense)
                                     == (row.entry.id, row.owner, row.sense)), None)
                    if matching is None:
                        event.accept()
                        return
                    index = self.model().index(matching, column)
                    row = index.data(Qt.ItemDataRole.UserRole)
            if related_hit or sense_hit:
                event.accept()
                self.setCurrentIndex(index)
                self.setFocus()
                count = len(row.word.senses) if sense_hit else len(row.entry.related)
                if count < 2 or not self.delegate.before_edit():
                    return
                # Draft handling can replace the model; never drag a different child by accident.
                matching = next((i for i, item in enumerate(self.model().rows)
                                 if item.entry.id == row.entry.id and item.owner == row.owner
                                 and item.sense == row.sense and item.word == row.word), None)
                if matching is None:
                    return
                self.setCurrentIndex(self.model().index(matching, column))
                self.setFocus()
                if sense_hit:
                    self._sense_drag = (row.entry.id, row.owner, row.sense)
                else:
                    self._related_drag = (row.entry.id, row.owner)
                self._drag_candidate = (row.entry.id, event.position().toPoint())
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
                return
            if column == 0 and row.first and not self.study_mode:
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
            if not row.hidden and column == 3 and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.set_entry_revealed(row.entry.id, False)
                event.accept()
                return
            if self.delegate.copy_hit(index, event.position()):
                self._copy_press = (QPersistentModelIndex(index), event.position().toPoint())
        super().mousePressEvent(event)

    def _reveal_mask(self, index, point):
        if not self.delegate.mask_hit(index, point):
            return False
        entry_id = index.data(Qt.ItemDataRole.UserRole).entry.id
        if self.delegate.submit():
            self._just_revealed = (entry_id, time.monotonic())
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.set_entry_revealed(entry_id, True)
        return True

    def related_handle_hit(self, index, point):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None or row.hidden or row.owner < 0 or row.sense != 0 or index.column() != 1:
            return False
        rect = self.visualRect(index)
        # The related-word text starts at x + 26; its copy/edit area stays separate.
        return QRect(rect.left() + 6, rect.top() + row.top_padding, 20, 20).contains(point)

    def _begin_drag(self, point):
        entry_id, origin = self._drag_candidate
        self._drag_groups = []
        self._drag_static = []
        for number, row in enumerate(self.model().rows):
            if ((self._sense_drag and (row.entry.id != entry_id or row.owner != self._sense_drag[1]))
                    or (self._related_drag and (row.entry.id != entry_id or row.owner < 0))):
                self._drag_static.append([row.entry.id, number, number, self.rowHeight(number)])
                continue
            if self._sense_drag:
                self._drag_groups.append([row.sense, number, number, self.rowHeight(number)])
                continue
            if (row.sense == 0 if self._related_drag else row.first):
                self._drag_groups.append([row.owner if self._related_drag else row.entry.id, number, number, 0])
            self._drag_groups[-1][2] = number
            self._drag_groups[-1][3] += self.rowHeight(number)
        source_key = (self._sense_drag[2] if self._sense_drag else
                      (self._related_drag[1] if self._related_drag else entry_id))
        self._drag_source = next(i for i, group in enumerate(self._drag_groups) if group[0] == source_key)
        self._drag_target = self._drag_source
        top = self.rowViewportPosition(self._drag_groups[self._drag_source][1])
        self._drag_grab_offset = origin.y() - top
        self._drag_point = point
        self._drag_base = (self.rowViewportPosition(self._drag_groups[0][1]) + self.verticalScrollBar().value()
                           if self._related_drag or self._sense_drag else 0)
        self._drag_positions = group_positions(self._drag_groups, self._drag_base)
        self._drag_active = True
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self._drag_scroll.start()
        self._update_drag_target()

    def mouseMoveEvent(self, event):
        if (self._copy_press and
                (event.position().toPoint() - self._copy_press[1]).manhattanLength() >= QApplication.startDragDistance()):
            self._copy_press = None
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
        target = insertion_index(self._drag_groups, self._drag_source, top, self._drag_base)
        if target == self._drag_target:
            return
        self._drag_target = target
        reordered = list(others)
        reordered.insert(target, self._drag_groups[self._drag_source])
        self._drag_animation.stop()
        self._drag_starts = dict(self._drag_positions)
        self._drag_ends = group_positions(reordered, self._drag_base)
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
        delta = edge_scroll_delta(y, height)
        if delta:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + delta)
            self._update_drag_target()
            self.viewport().update()

    def cancel_drag(self):
        self._drag_candidate = None
        self._related_drag = None
        self._sense_drag = None
        self._drag_active = False
        self._drag_scroll.stop()
        self._drag_animation.stop()
        self.viewport().unsetCursor()
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._copy_press:
            index, origin = self._copy_press
            self._copy_press = None
            point = event.position().toPoint()
            if (index.isValid() and (point - origin).manhattanLength() < QApplication.startDragDistance()
                    and self.delegate.copy_hit(QModelIndex(index), point)):
                if self._pending_copy.isValid() and self._pending_copy != index:
                    self._copy_timer.stop()
                    self._emit_pending_copy()
                self._pending_copy = index
                self._copy_timer.start(QApplication.doubleClickInterval())
        if self._drag_candidate and event.button() == Qt.MouseButton.LeftButton:
            accepted = self._drag_active and self.viewport().rect().contains(event.position().toPoint())
            entry_id = self._drag_candidate[0]
            related = self._related_drag
            sense = self._sense_drag
            if accepted and (related or sense):
                content_y = event.position().y() + self.verticalScrollBar().value()
                accepted = self._drag_base <= content_y < self._drag_base + sum(g[3] for g in self._drag_groups)
            target = self._drag_target if self._drag_active else None
            changed = accepted and target != self._drag_source
            self.cancel_drag()
            if changed:
                if sense:
                    self.senseMoveRequested.emit(entry_id, sense[1], sense[2], target)
                elif related:
                    self.relatedMoveRequested.emit(entry_id, related[1], target)
                else:
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
        if (self.hasFocus() and self.delegate.editor is None
                and self.state() != QAbstractItemView.State.EditingState):
            key = event.key()
            modifiers = event.modifiers()
            if key == Qt.Key.Key_Enter:
                modifiers &= ~Qt.KeyboardModifier.KeypadModifier
            if (event.modifiers() == Qt.KeyboardModifier.ControlModifier
                    and key in (Qt.Key.Key_Up, Qt.Key.Key_Down)):
                action = (QAbstractSlider.SliderAction.SliderSingleStepSub if key == Qt.Key.Key_Up
                          else QAbstractSlider.SliderAction.SliderSingleStepAdd)
                self.verticalScrollBar().triggerAction(action)
                event.accept()
                return
            if modifiers != Qt.KeyboardModifier.NoModifier:
                super().keyPressEvent(event)
                return
            if key == Qt.Key.Key_F2:
                current = self.currentIndex()
                row = current.data(Qt.ItemDataRole.UserRole)
                if (not event.isAutoRepeat() and row is not None
                        and self.selectionModel().isSelected(current)):
                    self.edit_field((row.entry.id, row.owner, row.sense, 3))
                event.accept()
                return
            step = navigation_step(event)
            if step is not None:
                self.navigate_entries(step)
                event.accept()
                return
            study_enter = self.study_mode and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            if key == Qt.Key.Key_Space or study_enter:
                current = self.currentIndex()
                row = current.data(Qt.ItemDataRole.UserRole)
                if (not event.isAutoRepeat() and row is not None
                        and self.selectionModel().isSelected(current)):
                    if study_enter and not row.hidden:
                        self.actionRequested.emit(row.entry.id, "study")
                    else:
                        self.set_entry_revealed(row.entry.id, row.hidden)
                event.accept()
                return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.cancel_drag()
        super().focusOutEvent(event)

    def _paint_drag_group(self, painter, group, top, columns=range(5)):
        word_id, start, end, height = group
        selected = self.currentIndex().data(Qt.ItemDataRole.UserRole)
        for row in range(start, end + 1):
            row_height = self.rowHeight(row)
            if top + row_height >= 0 and top <= self.viewport().height():
                item = self.model().rows[row]
                for column in columns:
                    if item.hidden and column == 3:
                        continue
                    option = QStyleOptionViewItem()
                    self.initViewItemOption(option)
                    option.rect = QRect(self.columnViewportPosition(column), round(top),
                                        self.columnWidth(column) + (self.columnWidth(3) if item.hidden and column == 2 else 0),
                                        row_height)
                    if selected and selected.entry.id == item.entry.id:
                        option.state |= QStyle.StateFlag.State_Selected
                    self.delegate.paint(painter, option, self.model().index(row, column))
            top += row_height

    def _paint_drag(self):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(WordGroupStyle.BACKGROUND))
        scroll = self.verticalScrollBar().value()
        for group in self._drag_static:
            self._paint_drag_group(painter, group, self.rowViewportPosition(group[1]))
        columns = (2, 3) if self._sense_drag else range(self.model().columnCount())
        if self._sense_drag:
            for group in self._drag_groups:
                self._paint_drag_group(painter, group, self.rowViewportPosition(group[1]), (0, 1, 4))
        for i, group in enumerate(self._drag_groups):
            if i != self._drag_source:
                self._paint_drag_group(painter, group, self._drag_positions[group[0]] - scroll, columns)
        source = self._drag_groups[self._drag_source]
        top = self._drag_point.y() - self._drag_grab_offset
        if self._related_drag or self._sense_drag:
            base = self._drag_base - scroll
            top = max(base, min(top, base + sum(g[3] for g in self._drag_groups) - source[3]))
        self._paint_drag_group(painter, source, top, columns)

    def mouseDoubleClickEvent(self, event):
        self._cancel_pending_copy()
        self.cancel_drag()
        if event.button() != Qt.MouseButton.LeftButton:
            event.accept()
            return
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            if self.related_handle_hit(index, event.position().toPoint()):
                event.accept()
                return
            row = index.data(Qt.ItemDataRole.UserRole)
            if row.entry.id == self._just_revealed[0] and time.monotonic() - self._just_revealed[1] < 0.5:
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                event.accept()
                return
            if self.model().flags(index) & Qt.ItemFlag.ItemIsEditable:
                self._edit_pointer_field(index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _edit_pointer_field(self, index):
        """Right-click and double-click share validation and logical field lookup."""
        if not self.model().flags(index) & Qt.ItemFlag.ItemIsEditable:
            return
        row = index.data(Qt.ItemDataRole.UserRole)
        target = (row.entry.id, row.owner, row.sense, index.column())
        if self.delegate.editor is not None and not self.delegate.submit():
            return
        if not self.delegate.before_edit():
            return
        matching = next((i for i, item in enumerate(self.model().rows)
                         if (item.entry.id, item.owner, item.sense) == target[:3]), None)
        if matching is None:
            return
        index = self.model().index(matching, target[3])
        self.setCurrentIndex(index)
        self.edit(index)
        if isinstance(self.delegate.editor, PosPicker):
            self.delegate.editor.showPopup()

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            from PySide6.QtWidgets import QToolTip
            index = self.indexAt(event.pos())
            if index.isValid():
                row = index.data(Qt.ItemDataRole.UserRole)
                text = ""
                if self.related_handle_hit(index, event.pos()):
                    text = "按住拖动调整顺序"
                elif index.column() == 4 and row.first:
                    labels = {"learned": "恢复待记" if row.entry.learned else "标记已记下",
                              "study": "记下（回车）",
                              "edit": "编辑整组", "trash": "删除单词",
                              "drag": "按住拖动整个词条" if self.reorder_enabled else "搜索时不可调整排序"}
                    for name, rect in self.action_rects(self.visualRect(index)):
                        if rect.contains(event.pos()):
                            text = labels[name]
                elif index.column() == 0 and row.first:
                    text = (f"第 {index.data()} 组" if self.study_mode else
                            ("取消重点" if row.entry.important else "设为重点"))
                elif row.hidden and index.column() in (2, 3):
                    if self.delegate.mask_hit(index, event.pos()):
                        text = "点击查看词性、翻译和关联词"
                else:
                    text = index.data(Qt.ItemDataRole.ToolTipRole) or ""
                if text:
                    QToolTip.showText(event.globalPos(), text, self)
                else:
                    QToolTip.hideText()
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
                             self.empty_text if self.empty_text is not None else
                             "未找到匹配单词" if self.search_active else
                             "这里还没有单词\n在下方添加，开始积累你的单词本")

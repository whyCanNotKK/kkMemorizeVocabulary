import math

from PySide6.QtCore import (
    QEasingCurve, QEvent, QParallelAnimationGroup, QPauseAnimation, QPoint,
    QPropertyAnimation, QSequentialAnimationGroup, QRectF, QSize, Qt, Signal, QTimer,
)
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QStyleOptionComboBox, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from .domain import POS_OPTIONS, SHORTCUT_LABELS, RelatedWord, Sense, normalize_pos
from .theme import MUTED, draw_related_arrow, draw_symbol, icon
from .pos_picker import pos_shortcut

INPUT_HEIGHT = 36
WORD_WIDTH = 144
PHONETIC_WIDTH = 108
POS_WIDTH = 78
INPUT_SPACING = 10


def button(text, parent=None, quiet=False):
    result = QPushButton(text, parent)
    if quiet:
        result.setObjectName("quiet")
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    return result


def confirm_remove(parent, message: str) -> bool:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("确认移除")
    dialog.setText(message)
    dialog.setInformativeText("此操作会移除已填写的内容。")
    yes = dialog.addButton("确认移除", QMessageBox.ButtonRole.AcceptRole)
    no = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(yes)
    dialog.setEscapeButton(no)
    dialog.exec()
    return dialog.clickedButton() is yes


class FloatingNotice(QFrame):
    """A focus-preserving overlay above a control or at the window bottom."""
    actionTriggered = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("floatingNotice")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.action = button("查看已有词", quiet=True)
        self.action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.action.clicked.connect(self._activate)
        layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignHCenter)
        self.opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity)
        self.animation = QSequentialAnimationGroup(self)
        self.enter = QParallelAnimationGroup()
        self.leave = QParallelAnimationGroup()
        self.motion_in = QPropertyAnimation(self, b"pos")
        self.fade_in = QPropertyAnimation(self.opacity, b"opacity")
        self.motion_out = QPropertyAnimation(self, b"pos")
        self.fade_out = QPropertyAnimation(self.opacity, b"opacity")
        for animation in (self.motion_in, self.fade_in):
            animation.setDuration(180)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.enter.addAnimation(animation)
        for animation in (self.motion_out, self.fade_out):
            animation.setDuration(240)
            animation.setEasingCurve(QEasingCurve.Type.InCubic)
            self.leave.addAnimation(animation)
        self.animation.addAnimation(self.enter)
        self.animation.addAnimation(QPauseAnimation(2000))
        self.animation.addAnimation(self.leave)
        self.animation.finished.connect(self.hide)
        self.hide()

    def show_message(self, text, anchor=None, with_action=False):
        self.animation.stop()
        self.label.setText(text)
        self.action.setVisible(with_action)
        maximum = max(120, min(360, self.parentWidget().width() - 200))
        natural = max((self.label.fontMetrics().horizontalAdvance(line)
                       for line in text.splitlines()), default=0) + 2
        self.label.setFixedWidth(min(maximum, max(1, natural)))
        self.adjustSize()
        if anchor is None:
            point = QPoint(self.parentWidget().width() // 2,
                           self.parentWidget().height() - self.height() - 20)
        else:
            point = anchor.mapTo(self.parentWidget(), QPoint(anchor.width() // 2, -self.height() - 8))
        point.setX(max(12, min(point.x() - self.width() // 2, self.parentWidget().width() - self.width() - 12)))
        point.setY(max(12, point.y()))
        self.motion_in.setStartValue(point + QPoint(0, 12))
        self.motion_in.setEndValue(point)
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(1.0)
        self.motion_out.setStartValue(point)
        self.motion_out.setEndValue(point - QPoint(0, 18))
        self.fade_out.setStartValue(1.0)
        self.fade_out.setEndValue(0.0)
        self.opacity.setOpacity(0.0)
        self.move(point + QPoint(0, 12))
        self.show()
        self.raise_()
        self.animation.start()

    def dismiss(self):
        self.animation.stop()
        self.hide()

    def _activate(self):
        self.dismiss()
        self.actionTriggered.emit()


class PosOptionDelegate(QStyledItemDelegate):
    """Keep the numbered code left-aligned and the Chinese description right-aligned."""
    def paint(self, painter, option, index):
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        item.text = ''
        style = item.widget.style() if item.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, item.widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, item.widget)
        rect.adjust(4, 0, -4, 0)
        painter.save()
        painter.setFont(item.font)
        role = (QPalette.ColorRole.HighlightedText if item.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text)
        painter.setPen(item.palette.color(role))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f'{index.row() + 1}. {index.data(Qt.ItemDataRole.UserRole)}')
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         index.data(Qt.ItemDataRole.UserRole + 1))
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setWidth(size.width() + 16)
        return size


class PosCombo(QComboBox):
    """Numbers are visible only in the popup, not the selected value."""
    def __init__(self, parent=None):
        super().__init__(parent)
        for number, (code, label) in enumerate(POS_OPTIONS, 1):
            self.addItem(f"{number}. {code}    {label}", code)
            self.setItemData(number - 1, label, Qt.ItemDataRole.UserRole + 1)
        self.setCurrentIndex(-1)
        self.setPlaceholderText('词性')
        self.setFixedWidth(POS_WIDTH)
        self.setFixedHeight(INPUT_HEIGHT)
        self.setStyleSheet("QComboBox { padding: 7px 3px; padding-right: 0px; }"
                          "QComboBox::drop-down { width: 18px; }")
        self.view().setWordWrap(False)
        self.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self.view().setItemDelegate(PosOptionDelegate(self.view()))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMaxVisibleItems(9)
        self.view().installEventFilter(self)
        self.setAccessibleName("词性，数字 1 到 9 或 n、v、a 快速选择，a 切换形容词和副词，空格展开，Esc或Backspace清空")

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = self.currentData() or ""
        painter = QPainter(self)
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option, painter, self)
        if self.currentData():
            self.style().drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option, painter, self)
        else:
            rect = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                              QStyle.SubControl.SC_ComboBoxEditField, self)
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(rect.adjusted(6, 0, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             self.placeholderText())
        painter.setPen(QPen(QColor("#96a1b1"), 1.4))
        x, y = self.width() - 15, self.height() // 2
        painter.drawLine(x - 3, y - 2, x, y + 1)
        painter.drawLine(x, y + 1, x + 3, y - 2)

    def set_pos(self, pos):
        index = self.findData(normalize_pos(pos))
        self.setCurrentIndex(index)

    def _digit(self, event: QKeyEvent) -> bool:
        value = pos_shortcut(event, self.currentData())
        if value is None:
            return False
        self.set_pos(value)
        self.hidePopup()
        self.setFocus()
        return True

    def keyPressEvent(self, event):
        if self._clear_selection(event):
            return
        if self._digit(event):
            event.accept()
        elif event.key() == Qt.Key.Key_Space:
            self.showPopup()
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.view() and event.type() == QEvent.Type.KeyPress:
            if self._clear_selection(event):
                return True
            if self._digit(event):
                return True
        return super().eventFilter(obj, event)

    def _clear_selection(self, event):
        if (event.key() not in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace)
                or event.modifiers() != Qt.KeyboardModifier.NoModifier):
            return False
        self.setCurrentIndex(-1)
        self.hidePopup()
        self.setFocus()
        event.accept()
        return True

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ExpandingTextEdit(QPlainTextEdit):
    contentHeightChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.content_height = INPUT_HEIGHT
        self.document().setDocumentMargin(0)
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._measure_height)
        self.textChanged.connect(self._schedule_height)
        self.document().documentLayout().documentSizeChanged.connect(self._schedule_height)

    def _schedule_height(self, *args):
        self._height_timer.start(0)

    def _measure_height(self):
        # QPlainTextDocumentLayout.documentSize().height() counts lines, not pixels.
        layout = self.document().documentLayout()
        block = self.document().begin()
        height = 0
        while block.isValid():
            height += layout.blockBoundingRect(block).height()
            block = block.next()
        chrome = self.height() - self.viewport().height()
        required = max(INPUT_HEIGHT, math.ceil(height + chrome) + 2)
        if required != self.content_height:
            self.content_height = required
            self.contentHeightChanged.emit(required)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_height()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._schedule_height()


class SenseDragHandle(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, INPUT_HEIGHT)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("按住拖动排序")
        self.setAccessibleName("拖动调整释义顺序")

    def paintEvent(self, event):
        painter = QPainter(self)
        draw_symbol(painter, "drag", QRectF(3, 9, 18, 18), MUTED if self.isEnabled() else "#46505c")


class SenseRow(QWidget):
    changed = Signal()
    heightChanged = Signal()
    addRequested = Signal()
    removeRequested = Signal()

    def __init__(self, sense=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(INPUT_HEIGHT)
        sense = sense or Sense()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(INPUT_SPACING)
        self.pos = PosCombo()
        self.pos.set_pos(sense.pos)
        self.translation = ExpandingTextEdit()
        self.translation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.translation.setPlaceholderText("翻译（Enter 换行）")
        self.translation.setPlainText(sense.translation)
        self.translation.setTabChangesFocus(True)
        self.translation.setFixedHeight(INPUT_HEIGHT)
        self.translation.setAccessibleName("翻译")
        layout.addWidget(self.pos, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.translation, 1)
        self.drag_handle = SenseDragHandle()
        self.add_button = button("", quiet=True)
        self.remove_button = button("", quiet=True)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(2)
        tools.addWidget(self.drag_handle)
        for control, symbol, name, callback in (
                (self.add_button, "plus_circle", SHORTCUT_LABELS["add_sense"], self.addRequested.emit),
                (self.remove_button, "trash", SHORTCUT_LABELS["remove_sense"], self.removeRequested.emit)):
            control.setFixedSize(28, INPUT_HEIGHT)
            control.setIconSize(QSize(20, 20))
            control.setIcon(icon(symbol, disabled_color="#46505c" if symbol == "trash" else None))
            control.setStyleSheet("QPushButton { padding: 0; }")
            control.setAccessibleName(name)
            control.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            control.clicked.connect(lambda checked=False, callback=callback: callback())
            tools.addWidget(control)
        layout.addLayout(tools)
        tools.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.pos.currentIndexChanged.connect(self.changed)
        self.translation.textChanged.connect(self.changed)
        self.translation.contentHeightChanged.connect(self._resize_translation)

    def _resize_translation(self, height):
        self.translation.setFixedHeight(height)
        self.setFixedHeight(height)
        self.updateGeometry()
        self.heightChanged.emit()

    def value(self):
        return Sense(self.pos.currentData() or "", self.translation.toPlainText())

    def has_content(self):
        sense = self.value()
        return bool(sense.pos or sense.translation.strip())


class RelatedIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, INPUT_HEIGHT)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("按住拖动调整顺序")
        self.setAccessibleName("拖动调整关联词顺序")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_related_arrow(painter, 4, (self.height() - 7) // 2)


class WordBlock(QFrame):
    changed = Signal()
    focused = Signal(object)
    structureChanged = Signal()
    heightChanged = Signal()
    cursorMoved = Signal()

    def __init__(self, text="", senses=None, related=False, parent=None, *, phonetic=""):
        super().__init__(parent)
        self.setObjectName("wordBlock")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.related = related
        self.rows = []
        self._keys = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        if related:
            separator = QFrame()
            separator.setObjectName("wordSeparator")
            separator.setFixedHeight(1)
            outer.addWidget(separator)
        inputs = QHBoxLayout()
        inputs.setSpacing(INPUT_SPACING)
        self.word = QLineEdit(text)
        self.word.setPlaceholderText("关联词" if related else "单词")
        self.word.setFixedSize(WORD_WIDTH, INPUT_HEIGHT)
        self.word.setAccessibleName("关联词" if related else "主词")
        word_column = QWidget()
        word_column.setFixedWidth(WORD_WIDTH)
        word_layout = QHBoxLayout(word_column)
        word_layout.setContentsMargins(0, 0, 0, 0)
        word_layout.setSpacing(6)
        if related:
            self.indicator = RelatedIndicator()
            word_layout.addWidget(self.indicator)
            self.word.setFixedWidth(WORD_WIDTH - 22)
            word_layout.addWidget(self.word)
        else:
            word_layout.addWidget(self.word)
        self.phonetic = QLineEdit(phonetic)
        self.phonetic.setFixedSize(PHONETIC_WIDTH, INPUT_HEIGHT)
        self.phonetic.setPlaceholderText("音标")
        self.phonetic.setAccessibleName("关联词音标" if related else "主词音标")
        self.phonetic.textChanged.connect(self.changed)
        inputs.addWidget(word_column, 0, Qt.AlignmentFlag.AlignTop)
        inputs.addWidget(self.phonetic, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(7)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sense_layout = QVBoxLayout()
        self.sense_layout.setSpacing(8)
        right.addLayout(self.sense_layout)
        inputs.addLayout(right, 1)
        outer.addLayout(inputs)
        for sense in senses or [Sense()]:
            self._append_sense(sense)
        self.word.textChanged.connect(self.changed)
        self._refresh_actions()

    def _append_sense(self, sense):
        row = SenseRow(sense)
        row.changed.connect(self.changed)
        row.heightChanged.connect(self.heightChanged)
        row.translation.cursorPositionChanged.connect(self.cursorMoved)
        row.addRequested.connect(lambda: self.add_sense(after=row))
        row.removeRequested.connect(lambda: self.remove_sense(row))
        self.rows.append(row)
        self.sense_layout.addWidget(row)
        return row

    def add_sense(self, *, after=None):
        row = self._append_sense(Sense())
        if after is not None:
            self.rows.remove(row)
            index = self.rows.index(after) + 1
            self.rows.insert(index, row)
            self.sense_layout.removeWidget(row)
            self.sense_layout.insertWidget(index, row)
        self._refresh_actions()
        self.changed.emit()
        self.structureChanged.emit()
        QTimer.singleShot(0, row.pos.setFocus)

    def remove_last_sense(self):
        self.remove_sense(self.rows[-1])

    def remove_sense(self, row):
        if len(self.rows) <= 1:
            return
        if row.has_content() and not confirm_remove(self, "移除这条释义？"):
            return
        index = self.rows.index(row)
        self.rows.remove(row)
        self.sense_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._refresh_actions()
        self.changed.emit()
        self.structureChanged.emit()
        self.rows[min(index, len(self.rows) - 1)].translation.setFocus()

    def set_shortcuts(self, keys):
        self._keys = keys
        self._refresh_actions()

    def _refresh_actions(self):
        for row in self.rows:
            row.add_button.setToolTip(SHORTCUT_LABELS["add_sense"] + (f" · {self._keys['add_sense']}" if self._keys else ""))
            row.remove_button.setToolTip(SHORTCUT_LABELS["remove_sense"] + (f" · {self._keys['remove_sense']}" if self._keys else ""))
            row.remove_button.setEnabled(len(self.rows) > 1)
            row.drag_handle.setEnabled(len(self.rows) > 1)

    def focused_row(self):
        focus = QApplication.focusWidget()
        return next((row for row in self.rows if focus is row or focus and row.isAncestorOf(focus)), None)

    def move_sense(self, source, target):
        self.rows.insert(target, self.rows.pop(source))
        for index, row in enumerate(self.rows):
            self.sense_layout.removeWidget(row)
            self.sense_layout.insertWidget(index, row)
        self.changed.emit()
        self.structureChanged.emit()

    def value(self):
        return RelatedWord(self.word.text(), [row.value() for row in self.rows], self.phonetic.text())

    def has_content(self):
        return bool(self.word.text().strip() or self.phonetic.text().strip() or any(row.has_content() for row in self.rows))

    def input_order(self):
        result = [self.word, self.phonetic]
        for row in self.rows:
            result.extend([row.pos, row.translation])
        return result

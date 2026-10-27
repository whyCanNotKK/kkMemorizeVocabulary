from PySide6.QtCore import (
    QEasingCurve, QEvent, QParallelAnimationGroup, QPauseAnimation, QPoint,
    QPropertyAnimation, QSequentialAnimationGroup, Qt, Signal, QTimer,
)
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
)

from .domain import POS_OPTIONS, RelatedWord, Sense
from .theme import draw_related_arrow

INPUT_HEIGHT = 36
WORD_WIDTH = 192
POS_WIDTH = 104
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
    """A focus-preserving overlay anchored above a control, outside layouts."""
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

    def show_message(self, text, anchor, with_action=False):
        self.animation.stop()
        self.label.setText(text)
        self.action.setVisible(with_action)
        maximum = max(120, min(360, self.parentWidget().width() - 200))
        natural = max((self.label.fontMetrics().horizontalAdvance(line)
                       for line in text.splitlines()), default=0) + 2
        self.label.setFixedWidth(min(maximum, max(1, natural)))
        self.adjustSize()
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


class PosCombo(QComboBox):
    """Numbers are visible only in the popup, not the selected value."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.addItem("选择词性", "")
        for number, (code, label) in enumerate(POS_OPTIONS, 1):
            self.addItem(f"{number}    {code}    {label}", code)
        self.setFixedWidth(POS_WIDTH)
        self.setFixedHeight(INPUT_HEIGHT)
        self.view().setMinimumWidth(200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMaxVisibleItems(10)
        self.view().installEventFilter(self)
        self._opening_index = 0
        self.setAccessibleName("词性，数字 1 到 9 或 n、v、a 快速选择，a 切换形容词和副词，空格展开")

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = self.currentData() or "选择词性"
        painter = QPainter(self)
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option, painter, self)
        self.style().drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option, painter, self)
        painter.setPen(QPen(QColor("#96a1b1"), 1.4))
        x, y = self.width() - 15, self.height() // 2
        painter.drawLine(x - 3, y - 2, x, y + 1)
        painter.drawLine(x, y + 1, x + 3, y - 2)

    def set_pos(self, pos):
        index = self.findData(pos)
        self.setCurrentIndex(max(0, index))

    def showPopup(self):
        self._opening_index = self.currentIndex()
        super().showPopup()

    def _digit(self, event: QKeyEvent) -> bool:
        if not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            if Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9:
                self.setCurrentIndex(event.key() - Qt.Key.Key_0)
                self.hidePopup()
                self.setFocus()
                return True
            index = {Qt.Key.Key_N: 1, Qt.Key.Key_V: 2, Qt.Key.Key_A: 3}.get(event.key())
            if index is not None:
                if event.key() == Qt.Key.Key_A:
                    if event.isAutoRepeat():
                        return True
                    index = 4 if self.currentData() == "adj." else 3
                self.setCurrentIndex(index)
                self.hidePopup()
                self.setFocus()
                return True
        return False

    def keyPressEvent(self, event):
        if self._digit(event):
            event.accept()
        elif event.key() == Qt.Key.Key_Space:
            self.showPopup()
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.view() and event.type() == QEvent.Type.KeyPress:
            if self._digit(event):
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.setCurrentIndex(self._opening_index)
                self.hidePopup()
                self.setFocus()
                return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SenseRow(QWidget):
    changed = Signal()

    def __init__(self, sense=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(INPUT_HEIGHT)
        sense = sense or Sense()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(INPUT_SPACING)
        self.pos = PosCombo()
        self.pos.set_pos(sense.pos)
        self.translation = QPlainTextEdit()
        self.translation.document().setDocumentMargin(0)
        self.translation.setPlaceholderText("输入翻译，Enter 换行")
        self.translation.setPlainText(sense.translation)
        self.translation.setTabChangesFocus(True)
        self.translation.setFixedHeight(INPUT_HEIGHT)
        self.translation.setAccessibleName("翻译")
        layout.addWidget(self.pos, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.translation, 1)
        self.pos.currentIndexChanged.connect(self.changed)
        self.translation.textChanged.connect(self.changed)

    def value(self):
        return Sense(self.pos.currentData() or "", self.translation.toPlainText())

    def has_content(self):
        sense = self.value()
        return bool(sense.pos or sense.translation.strip())


class RelatedIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, INPUT_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("关联词")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_related_arrow(painter, 4, (self.height() - 7) // 2)


class WordBlock(QFrame):
    changed = Signal()
    focused = Signal(object)
    removeRequested = Signal(object)
    structureChanged = Signal()

    def __init__(self, text="", senses=None, related=False, parent=None):
        super().__init__(parent)
        self.setObjectName("wordBlock")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.related = related
        self.rows = []
        self._keys = {}
        self._active = False
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
        self.word.setPlaceholderText("关联词" if related else "输入主词")
        self.word.setFixedSize(WORD_WIDTH, INPUT_HEIGHT)
        self.word.setAccessibleName("关联词" if related else "主词")
        if related:
            word_column = QWidget()
            word_column.setFixedWidth(WORD_WIDTH)
            word_layout = QHBoxLayout(word_column)
            word_layout.setContentsMargins(0, 0, 0, 0)
            word_layout.setSpacing(6)
            word_layout.addWidget(RelatedIndicator())
            self.word.setFixedWidth(WORD_WIDTH - 22)
            word_layout.addWidget(self.word)
            inputs.addWidget(word_column, 0, Qt.AlignmentFlag.AlignTop)
        else:
            inputs.addWidget(self.word, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(7)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sense_layout = QVBoxLayout()
        self.sense_layout.setSpacing(8)
        right.addLayout(self.sense_layout)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.add_button = button("增加释义", quiet=True)
        self.remove_button = button("删除释义", quiet=True)
        self.add_button.clicked.connect(self.add_sense)
        self.remove_button.clicked.connect(self.remove_last_sense)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        self.remove_related_button = None
        if related:
            self.remove_related_button = button("移除", quiet=True)
            self.remove_related_button.setAccessibleName("移除这个关联词")
            self.remove_related_button.clicked.connect(lambda: self.removeRequested.emit(self))
            actions.addWidget(self.remove_related_button)
        right.addLayout(actions)
        inputs.addLayout(right, 1)
        outer.addLayout(inputs)
        for sense in senses or [Sense()]:
            self._append_sense(sense)
        self.word.textChanged.connect(self.changed)
        self._refresh_actions()

    def _append_sense(self, sense):
        row = SenseRow(sense)
        row.changed.connect(self.changed)
        self.rows.append(row)
        self.sense_layout.addWidget(row)
        return row

    def add_sense(self):
        row = self._append_sense(Sense())
        self._refresh_actions()
        self.changed.emit()
        self.structureChanged.emit()
        QTimer.singleShot(0, row.pos.setFocus)

    def remove_last_sense(self):
        if len(self.rows) <= 1:
            return
        row = self.rows[-1]
        if row.has_content() and not confirm_remove(self, "移除这个词的最后一条释义？"):
            return
        self.rows.pop()
        self.sense_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._refresh_actions()
        self.changed.emit()
        self.structureChanged.emit()
        self.rows[-1].translation.setFocus()

    def set_active(self, active, keys):
        self._active, self._keys = active, keys
        self._refresh_actions()

    def _refresh_actions(self):
        add_key = f"  {self._keys.get('add_sense', '')}" if self._active else ""
        remove_key = f"  {self._keys.get('remove_sense', '')}" if self._active else ""
        self.add_button.setText("增加释义" + add_key)
        self.remove_button.setText("删除释义" + remove_key)
        self.remove_button.setEnabled(len(self.rows) > 1)
        self.remove_button.setVisible(len(self.rows) > 1)

    def value(self):
        return RelatedWord(self.word.text(), [row.value() for row in self.rows])

    def has_content(self):
        return bool(self.word.text().strip() or any(row.has_content() for row in self.rows))

    def input_order(self):
        result = [self.word]
        for row in self.rows:
            result.extend([row.pos, row.translation])
        result.extend([self.add_button, self.remove_button])
        if self.remove_related_button:
            result.append(self.remove_related_button)
        return result

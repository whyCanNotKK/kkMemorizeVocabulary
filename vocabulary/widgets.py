from PySide6.QtCore import QEvent, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
)

from .domain import POS_OPTIONS, RelatedWord, Sense


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


class PosCombo(QComboBox):
    """Numbers are visible only in the popup, not the selected value."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.addItem("选择词性", "")
        for number, (code, label) in enumerate(POS_OPTIONS, 1):
            self.addItem(f"{number}    {code}    {label}", code)
        self.setMinimumWidth(136)
        self.setMaximumWidth(152)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMaxVisibleItems(10)
        self.view().installEventFilter(self)
        self._opening_index = 0
        self.setAccessibleName("词性，数字 1 到 9 快速选择，空格展开")

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
        self.setFixedHeight(66)
        sense = sense or Sense()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.pos = PosCombo()
        self.pos.set_pos(sense.pos)
        self.translation = QPlainTextEdit()
        self.translation.setPlaceholderText("输入翻译，Enter 换行")
        self.translation.setPlainText(sense.translation)
        self.translation.setTabChangesFocus(True)
        self.translation.setFixedHeight(66)
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


class WordBlock(QFrame):
    changed = Signal()
    focused = Signal(object)
    removeRequested = Signal(object)
    structureChanged = Signal()

    def __init__(self, text="", senses=None, related=False, parent=None):
        super().__init__(parent)
        self.setObjectName("wordBlock")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setProperty("focused", False)
        self.related = related
        self.rows = []
        self._keys = {}
        self._active = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(8)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        if related:
            header = QHBoxLayout()
            self.label = QLabel("关联词")
            self.label.setObjectName("muted")
            remove = button("移除", quiet=True)
            remove.setAccessibleName("移除这个关联词")
            remove.clicked.connect(lambda: self.removeRequested.emit(self))
            header.addWidget(self.label)
            header.addStretch()
            header.addWidget(remove)
            outer.addLayout(header)
        inputs = QHBoxLayout()
        inputs.setSpacing(12)
        self.word = QLineEdit(text)
        self.word.setPlaceholderText("关联词" if related else "输入主词")
        self.word.setFixedWidth(192)
        self.word.setAccessibleName("关联词" if related else "主词")
        inputs.addWidget(self.word, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(7)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sense_layout = QVBoxLayout()
        self.sense_layout.setSpacing(8)
        right.addLayout(self.sense_layout)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.add_button = button("＋ 释义", quiet=True)
        self.remove_button = button("− 末项", quiet=True)
        self.add_button.clicked.connect(self.add_sense)
        self.remove_button.clicked.connect(self.remove_last_sense)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
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
        self.setProperty("focused", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self._refresh_actions()

    def _refresh_actions(self):
        add_key = f"  {self._keys.get('add_sense', '')}" if self._active else ""
        remove_key = f"  {self._keys.get('remove_sense', '')}" if self._active else ""
        self.add_button.setText("＋ 释义" + add_key)
        self.remove_button.setText("− 末项" + remove_key)
        self.remove_button.setEnabled(len(self.rows) > 1)

    def value(self):
        return RelatedWord(self.word.text(), [row.value() for row in self.rows])

    def has_content(self):
        return bool(self.word.text().strip() or any(row.has_content() for row in self.rows))

    def input_order(self):
        result = [self.word]
        for row in self.rows:
            result.extend([row.pos, row.translation])
        result.extend([self.add_button, self.remove_button])
        return result

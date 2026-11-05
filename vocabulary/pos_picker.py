"""A complete POS popup anchored to a cell; persistence belongs to the delegate."""
import shiboken6
from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .domain import POS_OPTIONS, normalize_pos


def pos_shortcut(event, current):
    if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
        return None
    if Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9:
        return POS_OPTIONS[event.key() - Qt.Key.Key_1][0]
    if event.key() == Qt.Key.Key_N:
        return "n."
    if event.key() == Qt.Key.Key_V:
        return "v."
    if event.key() == Qt.Key.Key_A:
        return current if event.isAutoRepeat() else "adv." if current == "adj." else "adj."
    return None


class PosPicker(QWidget):
    chosen = Signal()
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._anchor = QRect()
        self._value = ""
        self._hiding = False
        self.popup = QFrame(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.popup.setObjectName("posPicker")
        self.popup.setStyleSheet(
            "QFrame#posPicker { background: #252e39; border: 1px solid #536476; }"
            "QListWidget { background: transparent; border: none; padding: 0; }"
            "QListWidget::item { padding: 4px 6px; }"
            "QListWidget::item:selected { background: #3b625b; color: #ffffff; }")
        layout = QVBoxLayout(self.popup)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.options = QListWidget()
        self.options.setFrameShape(QFrame.Shape.NoFrame)
        self.options.setWordWrap(False)
        self.options.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.options.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.options.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for number, (code, label) in enumerate(POS_OPTIONS, 1):
            item = QListWidgetItem(f"{number}    {code}    {label}")
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.options.addItem(item)
        layout.addWidget(self.options)
        self.options.itemClicked.connect(lambda item: self.chosen.emit())
        self.options.installEventFilter(self)
        self.popup.installEventFilter(self)

    def view(self):
        return self.options

    def count(self):
        return self.options.count()

    def itemText(self, index):
        return self.options.item(index).text()

    def currentData(self):
        item = self.options.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else self._value

    def set_pos(self, value):
        value = normalize_pos(value)
        self._value = value
        self.options.setCurrentRow(next((i for i, (code, label) in enumerate(POS_OPTIONS) if code == value), -1))

    def set_anchor(self, rect):
        self._anchor = QRect(rect)

    def showPopup(self):
        if not shiboken6.isValid(self) or not self.isVisible():
            return
        self.options.ensurePolished()
        self.options.doItemsLayout()
        width = self.options.sizeHintForColumn(0)
        height = sum(self.options.sizeHintForRow(i) for i in range(self.count()))
        self.options.setFixedSize(width, height)
        self.popup.setFixedSize(width + 2, height + 2)
        anchor = self._anchor
        screen = QApplication.screenAt(anchor.center()) or self.screen()
        bounds = screen.availableGeometry()
        x = anchor.center().x() - self.popup.width() // 2
        x = max(bounds.left(), min(x, bounds.right() - self.popup.width() + 1))
        y = anchor.bottom() + 1
        if y + self.popup.height() > bounds.bottom() + 1:
            y = anchor.top() - self.popup.height()
        y = max(bounds.top(), min(y, bounds.bottom() - self.popup.height() + 1))
        self.popup.move(x, y)
        self.popup.show()
        self.options.doItemsLayout()
        self.options.scrollToTop()
        self.options.setFocus(Qt.FocusReason.PopupFocusReason)

    def hidePopup(self):
        self._hiding = True
        try:
            self.popup.hide()
        finally:
            self._hiding = False

    def paintEvent(self, event):
        # The table's normal POS label remains visible under the floating picker.
        pass

    def eventFilter(self, source, event):
        if source is self.popup and event.type() == QEvent.Type.Hide and not self._hiding:
            self.cancelled.emit()
        if source is self.options and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.cancelled.emit()
                return True
            value = pos_shortcut(event, self.currentData())
            if value is not None:
                if not event.isAutoRepeat():
                    self.set_pos(value)
                    self.chosen.emit()
                return True
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and not event.isAutoRepeat()
                    and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier):
                self.chosen.emit()
                return True
        return False

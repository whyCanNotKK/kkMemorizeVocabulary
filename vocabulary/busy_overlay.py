"""Window-wide busy feedback while a background task keeps Qt responsive."""
from contextlib import contextmanager

from PySide6.QtCore import QEvent, QEventLoop, QRectF, QThread, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .theme import ACCENT, TEXT


class _Task(QThread):
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.value = None
        self.error = None

    def run(self):
        try:
            self.value = self.operation()
        except BaseException as error:
            self.error = error


class BusyOverlay(QWidget):
    """Block input without changing focus; this must not commit an active editor."""
    _INPUT_EVENTS = frozenset((
        QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel, QEvent.Type.ContextMenu,
        QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.Shortcut,
        QEvent.Type.ShortcutOverride, QEvent.Type.InputMethod,
        QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd,
    ))

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('busyOverlay')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName('正在处理，请稍候')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.message = ''
        self.angle = 0
        self.active = False
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._advance)
        self.hide()
        parent.installEventFilter(self)

    def _advance(self):
        self.angle = (self.angle + 6) % 360
        self.update()

    def eventFilter(self, target, event):
        owner = self.parentWidget()
        if target is owner and event.type() == QEvent.Type.Resize:
            self.setGeometry(owner.rect())
        if self.active:
            if target is owner and event.type() == QEvent.Type.Close:
                event.ignore()
                return True
            if (event.type() in self._INPUT_EVENTS and isinstance(target, QWidget)
                    and (target is owner or owner.isAncestorOf(target))):
                event.accept()
                return True
        return False

    @contextmanager
    def display(self, message):
        if self.active:
            raise RuntimeError('已有导入或导出任务正在处理。')
        self.active = True
        self.message = message
        self.setAccessibleDescription(message)
        self.setGeometry(self.parentWidget().rect())
        app = QApplication.instance()
        app.installEventFilter(self)
        self.show()
        self.raise_()
        self.timer.start()
        try:
            yield
        finally:
            self.timer.stop()
            self.hide()
            self.active = False
            app.removeEventFilter(self)

    def run(self, message, operation):
        """Return/raise on the GUI thread after the worker has fully stopped."""
        with self.display(message):
            task = _Task(operation, self)
            loop = QEventLoop(self)
            task.finished.connect(loop.quit)
            # Start only once the nested event loop can receive the finished signal.
            QTimer.singleShot(0, task.start)
            try:
                loop.exec()
                task.wait()
                if task.error is not None:
                    raise task.error
                return task.value
            finally:
                task.wait()
                task.deleteLater()
                loop.deleteLater()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(12, 17, 24, 174))
        x, y = self.width() / 2, self.height() / 2 - 15
        ring = QRectF(x - 23, y - 23, 46, 46)
        painter.setPen(QPen(QColor(164, 221, 197, 38), 4))
        painter.drawEllipse(ring)
        painter.setPen(QPen(QColor(ACCENT), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(ring, -self.angle * 16, 105 * 16)
        font = self.font()
        font.setPixelSize(14)
        painter.setFont(font)
        painter.setPen(QColor(TEXT))
        painter.drawText(QRectF(16, y + 42, self.width() - 32, 28),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.message)

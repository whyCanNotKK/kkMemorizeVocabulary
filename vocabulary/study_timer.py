"""One in-memory task clock; persisted status remains incomplete/complete only."""
import sys
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication


ACTIVITY_EVENTS = frozenset((QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                             QEvent.Type.Wheel, QEvent.Type.KeyPress, QEvent.Type.InputMethod))


class PowerEvents(QAbstractNativeEventFilter):
    def __init__(self, timer):
        super().__init__()
        self.timer = timer

    def nativeEventFilter(self, event_type, message):
        import ctypes
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0218 and msg.wParam in (4, 6, 7, 0x12):
            self.timer.suspend()
        return False, 0


class StudyTimer(QObject):
    changed = Signal()
    error = Signal(str)
    IDLE_NS = 60_000_000_000

    def __init__(self, repository, parent=None, *, clock=None):
        super().__init__(parent)
        self.repository = repository
        self.clock = clock or time.monotonic_ns
        self.context = None
        self.history = None
        self.running = False
        self._base_seconds = 0
        self._started_ns = 0
        self._activity_ns = 0
        self._power_events = None
        self._disposed = False
        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self.check_idle)
        app = QApplication.instance()
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.shutdown)
        if sys.platform == 'win32' and app.platformName() == 'windows':
            self._power_events = PowerEvents(self)
            app.installNativeEventFilter(self._power_events)

    def bind(self, context):
        history = self.repository.study_task_history(context)
        if self.context != context:
            self.pause()
            self.context = context
            self.history = history
            self._base_seconds = history.duration_seconds if history else 0
        else:
            self.history = history
            if not self.running and history is not None:
                self._base_seconds = max(self._base_seconds, history.duration_seconds)
        self.changed.emit()

    def start(self):
        if self.context is None:
            return False
        if self.running:
            return True
        # Also retries a previously failed pause write before starting another segment.
        self.checkpoint()
        history = self.repository.start_study_task(self.context)
        if history is None or history.status:
            return False
        self.history = history
        self._base_seconds = history.duration_seconds
        self._started_ns = self._activity_ns = self.clock()
        self.running = True
        self._poll.start()
        self.changed.emit()
        return True

    def duration_seconds(self):
        if not self.running:
            return self._base_seconds
        end = min(self.clock(), self._activity_ns + self.IDLE_NS)
        # Nonnegative half-up rounding, including exactly 0.5s (not banker's rounding).
        return self._base_seconds + (max(0, end - self._started_ns) + 500_000_000) // 1_000_000_000

    def checkpoint(self):
        if self.context is not None and self.history is not None and not self.history.status:
            self.repository.save_study_duration(self.context, self.duration_seconds())

    def pause(self):
        if self.running:
            self._base_seconds = self.duration_seconds()
            self.running = False
            self._poll.stop()
        try:
            self.checkpoint()
        finally:
            # Observers read committed database values, including after a failed save.
            self.changed.emit()

    def completed(self, history):
        """Adopt the committed result without another fallible database operation."""
        self.history = history
        if self.history is not None and self.history.status:
            self._base_seconds = self.history.duration_seconds
            self.running = False
        elif self.history is not None and not self.running:
            self._base_seconds = self.history.duration_seconds
            self._started_ns = self._activity_ns = self.clock()
            self.running = True
        if self.running:
            self._poll.start()
        else:
            self._poll.stop()
        self.changed.emit()

    def reset(self):
        """Discard the replaced task clock after a successful full restore."""
        self.context = self.history = None
        self.running = False
        self._poll.stop()
        self._base_seconds = 0
        self.changed.emit()

    def check_idle(self):
        if self.running and self.clock() - self._activity_ns >= self.IDLE_NS:
            self.suspend()

    def suspend(self):
        try:
            self.pause()
        except Exception as error:
            # Keep the frozen whole-second value in memory for an explicit retry.
            self.error.emit(str(error))

    def note_activity(self):
        if self.running:
            self.check_idle()
            self._activity_ns = self.clock()

    def eventFilter(self, watched, event):
        if self.running and event.type() in ACTIVITY_EVENTS:
            self.note_activity()
        return False

    def shutdown(self):
        self.suspend()
        self.dispose()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._poll.stop()
        app = QApplication.instance()
        app.removeEventFilter(self)
        if self._power_events is not None:
            app.removeNativeEventFilter(self._power_events)

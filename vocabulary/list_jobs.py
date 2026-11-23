"""Non-blocking, latest-request-only word-page reads on a thread-owned connection."""
from dataclasses import dataclass, field
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .list_query import PageReader, normalize_keyword


@dataclass
class PageRequest:
    request_id: int
    revision: int
    category: str
    keyword: str = ""
    page_number: int | None = 1
    anchor_id: int | None = None
    intent: object = None
    _cancelled: Event = field(default_factory=Event, repr=False, compare=False)


class _PageWorker(QObject):
    finished = Signal(object, object, object)

    def __init__(self, path, reader_factory):
        super().__init__()
        self.path = path
        self.reader_factory = reader_factory
        self.reader = None

    @Slot(object)
    def read(self, request):
        result, error = None, None
        connection = None
        try:
            if not request._cancelled.is_set():
                if self.reader is None:
                    self.reader = self.reader_factory(self.path)
                connection = getattr(self.reader, "connection", None)
                if connection is not None:
                    connection.set_progress_handler(lambda: int(request._cancelled.is_set()), 1000)
                result = self.reader.query_page(
                    request.category, request.keyword, request.page_number,
                    request.anchor_id)
        except Exception as exc:
            error = exc
        finally:
            if connection is not None:
                connection.set_progress_handler(None, 0)
        self.finished.emit(request, result, error)

    @Slot()
    def close(self):
        if self.reader is not None:
            self.reader.close()
            self.reader = None


class ListJobs(QObject):
    success = Signal(object, object)
    failure = Signal(object, object)
    busyChanged = Signal(bool)
    _dispatch = Signal(object)

    def __init__(self, path, parent=None, *, reader_factory=None):
        super().__init__(parent)
        self.busy = False
        self.latest_request = None
        self._serial = 0
        self._running = None
        self._pending = None
        self._closed = False
        self._events = set()
        self._thread = QThread()
        self._worker = _PageWorker(path, reader_factory or PageReader)
        self._worker.moveToThread(self._thread)
        self._dispatch.connect(self._worker.read)
        self._worker.finished.connect(self._finished)
        self._thread.finished.connect(self._worker.close)
        self._thread.finished.connect(self._worker.deleteLater)
        thread, events = self._thread, self._events

        def cleanup():
            for event in tuple(events):
                event.set()
            try:
                thread.quit()
                thread.wait()
            except RuntimeError:
                pass  # Explicit shutdown may already have deleted the Qt thread object.

        self.destroyed.connect(cleanup)
        self._thread.start()

    def request(self, category, keyword="", page_number=1, anchor_id=None, *, revision=0, intent=None):
        if self._closed:
            raise RuntimeError("分页查询器已关闭。")
        self._serial += 1
        request = PageRequest(self._serial, revision, category, normalize_keyword(keyword),
                              page_number, anchor_id, intent)
        self.latest_request = request
        self._events.add(request._cancelled)
        if self._running is not None:
            self._running._cancelled.set()
            if self._pending is not None:
                self._pending._cancelled.set()
                self._events.discard(self._pending._cancelled)
            self._pending = request
        else:
            self._start(request)
        return request

    def _set_busy(self, value):
        if self.busy != value:
            self.busy = value
            self.busyChanged.emit(value)

    def _start(self, request):
        self._running = request
        self._set_busy(True)
        self._dispatch.emit(request)

    @Slot(object, object, object)
    def _finished(self, request, result, error):
        self._events.discard(request._cancelled)
        if self._closed:
            return
        if self._running is request:
            self._running = None
        if self.latest_request is request and not request._cancelled.is_set():
            if error is None:
                self.success.emit(request, result)
            else:
                self.failure.emit(request, error)
        # A success/failure consumer may synchronously submit the next request.
        if self._running is None and self._pending is not None:
            pending, self._pending = self._pending, None
            self._start(pending)
        if self._running is None:
            self._set_busy(False)

    def cancel(self):
        """Invalidate even an already queued completion without blocking the GUI."""
        self.latest_request = None
        for event in tuple(self._events):
            event.set()
        if self._pending is not None:
            self._events.discard(self._pending._cancelled)
            self._pending = None
        if self._running is None:
            self._set_busy(False)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.cancel()
        self._thread.quit()
        self._thread.wait()
        self._running = None
        self._events.clear()
        self._set_busy(False)

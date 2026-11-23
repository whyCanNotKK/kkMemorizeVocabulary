import os
from shutil import copyfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from vocabulary.storage import Repository
from vocabulary.theme import apply_theme
from vocabulary.window import MainWindow


@pytest.fixture(scope="session")
def empty_database(tmp_path_factory):
    # Build the current empty schema once. Migration tests create their own
    # databases; other tests still get an independent on-disk database.
    path = tmp_path_factory.mktemp("empty-database") / "vocabulary.sqlite3"
    value = Repository(path)
    value.close()
    return path


@pytest.fixture
def repo(tmp_path, empty_database):
    path = tmp_path / "vocabulary.sqlite3"
    copyfile(empty_database, path)
    value = Repository(path)
    yield value
    value.close()


@pytest.fixture(scope="session")
def themed_app(qapp):
    apply_theme(qapp)
    return qapp


@pytest.fixture
def window(qtbot, themed_app, repo, monkeypatch):
    # Established interaction tests assert each UI action synchronously. Keep
    # their real SQL queries, and exercise worker/debounce ordering separately
    # in the dedicated asynchronous integration tests.
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtWidgets import QApplication
    from vocabulary.list_jobs import PageRequest
    from vocabulary.list_query import PageReader, normalize_keyword

    class ImmediateListJobs(QObject):
        success = Signal(object, object)
        failure = Signal(object, object)
        busyChanged = Signal(bool)

        def __init__(self, path, parent=None):
            super().__init__(parent)
            self.reader = PageReader(path)
            self.latest_request = None
            self.busy = False
            self.serial = 0

        def request(self, category, keyword='', page_number=1, anchor_id=None,
                    *, revision=0, intent=None):
            self.serial += 1
            request = PageRequest(self.serial, revision, category, normalize_keyword(keyword),
                                  page_number, anchor_id, intent)
            self.latest_request = request
            self.busy = True
            self.busyChanged.emit(True)
            try:
                result = self.reader.query_page(category, keyword, page_number, anchor_id)
            except Exception as error:
                self.failure.emit(request, error)
            else:
                self.success.emit(request, result)
            finally:
                self.busy = False
                self.busyChanged.emit(False)
            return request

        def cancel(self):
            self.latest_request = None

        def shutdown(self):
            if self.reader is not None:
                self.reader.close()
                self.reader = None
            self.cancel()
            self.busy = False

    monkeypatch.setattr('vocabulary.list_controller.ListJobs', ImmediateListJobs)
    value = MainWindow(repo)
    monkeypatch.setattr(value._search_timer, 'start', lambda *args: value.run_search())
    def before_close(widget):
        widget.table.delegate.cancel()
        widget.study_page.table.delegate.cancel()
        widget.table.cancel_drag()
        widget.study_page.table.cancel_drag()
        # QTest.mouseDClick emits its press without the final release. Finish
        # synthetic gestures so the next test's mouseMove updates QCursor,
        # rather than delivering a drag at a stale global cursor position.
        for mouse_button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton,
                             Qt.MouseButton.MiddleButton):
            if QApplication.mouseButtons() & mouse_button:
                qtbot.mouseRelease(widget, mouse_button, pos=widget.rect().bottomRight())
        widget._baseline = widget.form_value()
    qtbot.addWidget(value, before_close_func=before_close)
    value.show()
    value.activateWindow()
    qtbot.wait(30)
    yield value
    value._baseline = value.form_value()
    value.table.delegate.cancel()
    value._closing = True
    value.list_jobs.shutdown()
    value.hide()


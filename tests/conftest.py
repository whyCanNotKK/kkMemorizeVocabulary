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
def window(qtbot, themed_app, repo):
    value = MainWindow(repo)
    def before_close(widget):
        widget.table.delegate.cancel()
        widget._baseline = widget.form_value()
    qtbot.addWidget(value, before_close_func=before_close)
    value.show()
    value.activateWindow()
    qtbot.wait(30)
    yield value
    value._baseline = value.form_value()
    value.table.delegate.cancel()
    value._closing = True
    value.hide()


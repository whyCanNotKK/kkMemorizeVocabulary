import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from vocabulary.storage import Repository
from vocabulary.theme import apply_theme
from vocabulary.window import MainWindow


@pytest.fixture
def repo(tmp_path):
    value = Repository(tmp_path / "vocabulary.sqlite3")
    yield value
    value.close()


@pytest.fixture
def window(qtbot, qapp, repo):
    apply_theme(qapp)
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


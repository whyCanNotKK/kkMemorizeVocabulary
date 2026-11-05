from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QFrame, QWidget

from vocabulary.form_focus import FormFocusContext


def test_destroyed_panel_stops_application_focus_callbacks(qapp, qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    panel = QFrame(window)
    context = FormFocusContext(panel, window)
    context._set_active(True)
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(panel, QEvent.Type.DeferredDelete)

    # The panel can be destroyed before the context during window teardown.
    # A later global focus notification must not access the deleted widget.
    qapp.focusChanged.emit(None, None)
    assert not context.active

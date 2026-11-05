"""Form context survives passive clicks without retaining an old input target."""
from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
    QApplication, QComboBox, QKeySequenceEdit, QLineEdit, QPlainTextEdit,
    QScrollArea, QTextEdit, QWidget,
)

from .widgets import RelatedIndicator, SenseDragHandle


class FormFocusContext(QObject):
    changed = Signal()
    blankPressed = Signal()

    def __init__(self, panel, window):
        super().__init__(window)
        self.panel, self.window = panel, window
        self.active = False
        panel.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        app = QApplication.instance()
        app.focusChanged.connect(self._focus_changed)
        app.installEventFilter(self)
        panel.destroyed.connect(self._panel_destroyed)

    @Slot()
    def _panel_destroyed(self):
        # The panel may die before this window-owned context. Global focus
        # notifications during the rest of teardown must not access it.
        app = QApplication.instance()
        app.focusChanged.disconnect(self._focus_changed)
        app.removeEventFilter(self)
        self.active = False

    @staticmethod
    def _belongs_to(widget, ancestor):
        while isinstance(widget, QWidget):
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False

    def contains(self, widget):
        return self._belongs_to(widget, self.panel)

    def _set_active(self, active):
        if self.active != active:
            self.active = active
            self.changed.emit()

    @Slot(QWidget, QWidget)
    def _focus_changed(self, old, new):
        self._set_active(self.panel.isVisible() and self.contains(new))

    @staticmethod
    def _interactive(widget, boundary):
        controls = (QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
                    QComboBox, QKeySequenceEdit, QLineEdit, QPlainTextEdit, QTextEdit,
                    RelatedIndicator, SenseDragHandle)
        while isinstance(widget, QWidget) and widget is not boundary:
            if isinstance(widget, controls):
                return True
            if widget.focusPolicy() != Qt.FocusPolicy.NoFocus and not isinstance(widget, QScrollArea):
                return True
            widget = widget.parentWidget()
        return False

    def eventFilter(self, source, event):
        kind = event.type()
        app = QApplication.instance()
        if source is self.panel and kind == QEvent.Type.Hide:
            self._set_active(False)
        elif source is self.window and kind == QEvent.Type.WindowDeactivate:
            if not self.contains(app.activePopupWidget()):
                self._set_active(False)
        elif source is self.window and kind == QEvent.Type.WindowActivate:
            self._set_active(self.panel.isVisible() and self.contains(app.focusWidget()))
        elif kind == QEvent.Type.MouseButtonPress and isinstance(source, QWidget):
            hit = source.childAt(event.position().toPoint()) or source
            inside = self.panel.isVisible() and self.contains(hit)
            self._set_active(inside)
            if inside and not self._interactive(hit, self.panel):
                self.blankPressed.emit()
                self.panel.setFocus(Qt.FocusReason.MouseFocusReason)
                # Passive child widgets otherwise propagate the same press outside
                # the panel and would immediately discard the form context again.
                return True
            if (not inside and self.contains(app.focusWidget())
                    and self._belongs_to(hit, self.window) and not self._interactive(hit, self.window)):
                self.window.setFocus(Qt.FocusReason.MouseFocusReason)
        return False

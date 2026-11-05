"""Drag previews for existing form widgets; the owner applies the final order."""
from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from .drag_reorder import ANIMATION_MS, edge_scroll_delta, group_positions, insertion_index
from .theme import PANEL


class _Preview(QWidget):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def paintEvent(self, event):
        self.controller.paint(QPainter(self))


class FormReorderDrag(QObject):
    relatedMoved = Signal(int, int)
    senseMoved = Signal(object, int, int)
    activeChanged = Signal(bool)

    def __init__(self, scroll, blocks, prepare):
        super().__init__(scroll)
        self.scroll, self.blocks, self.prepare = scroll, blocks, prepare
        self.candidate = None
        self.active = False
        self.preview = _Preview(self, scroll.viewport())
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(ANIMATION_MS)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(self._animate)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._scroll)
        QApplication.instance().installEventFilter(self)

    def cancel(self):
        was_active = self.active
        if self.candidate is not None:
            self.handle.setCursor(Qt.CursorShape.OpenHandCursor)
        self.candidate = None
        self.active = False
        self.animation.stop()
        self.timer.stop()
        self.preview.hide()
        self.scroll.viewport().unsetCursor()
        self.images = {}
        self.backgrounds = {}
        if was_active:
            self.activeChanged.emit(False)

    def _point(self, event):
        return self.scroll.viewport().mapFromGlobal(event.globalPosition().toPoint())

    def eventFilter(self, source, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            children = self.blocks()[1:]
            block = next((block for block in children if source is block.indicator), None)
            if block is not None:
                if len(children) > 1 and self.prepare():
                    self.kind, self.owner, self.items = "related", None, list(children)
                    self.handle = source
                    self.candidate = (block, self._point(event))
                return True
            for owner in self.blocks():
                row = next((row for row in owner.rows if source is row.drag_handle), None)
                if row is not None:
                    if len(owner.rows) > 1 and self.prepare():
                        self.kind, self.owner, self.items = "sense", owner, list(owner.rows)
                        self.handle = source
                        self.candidate = (row, self._point(event))
                    return True
        if self.candidate is None:
            return False
        if kind == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel()
            return True
        if (kind == QEvent.Type.WindowDeactivate and source is self.scroll.window()
                or source is self.scroll.viewport() and kind in (QEvent.Type.Hide, QEvent.Type.Resize)):
            self.cancel()
            return False
        if kind == QEvent.Type.MouseMove:
            self.point = self._point(event)
            if not self.active and (self.point - self.candidate[1]).manhattanLength() >= QApplication.startDragDistance():
                self._begin()
            if self.active:
                self._update_target()
                self.preview.update()
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            point = self._point(event)
            accepted = self.active and self.scroll.viewport().rect().contains(point)
            if accepted:
                y = point.y() + self.scroll.verticalScrollBar().value()
                accepted = self.base <= y < self.base + self.scope_height
            source_index, target = (self.source, self.target) if self.active else (0, 0)
            kind, owner = self.kind, self.owner
            self.cancel()
            if accepted and source_index != target:
                if kind == "sense":
                    self.senseMoved.emit(owner, source_index, target)
                else:
                    self.relatedMoved.emit(source_index, target)
            return True
        if self.active and kind in (QEvent.Type.Wheel, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return True
        return False

    def _begin(self):
        blocks = list(self.blocks())
        items = blocks[1:] if self.kind == "related" else self.owner.rows if self.owner in blocks else []
        if self.candidate[0] not in items or list(items) != self.items:
            self.cancel()
            return
        spacing = (self.scroll.widget().layout().spacing() if self.kind == "related"
                   else self.owner.sense_layout.spacing())
        self.groups = [[item, 0, 0, item.height() + spacing] for item in self.items]
        self.scope_height = sum(group[3] for group in self.groups) - spacing
        self.source = self.items.index(self.candidate[0])
        self.target = self.source
        bar = self.scroll.verticalScrollBar()
        self.base = self.items[0].mapTo(self.scroll.viewport(), QPoint()).y() + bar.value()
        top = self.items[self.source].mapTo(self.scroll.viewport(), QPoint()).y()
        self.grab_offset = self.candidate[1].y() - top
        self.positions = group_positions(self.groups, self.base)
        self.backgrounds = {block: self._snapshot(block) for block in blocks}
        self.images = {item: self._snapshot(item) for item in self.items}
        self.active = True
        self.handle.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.activeChanged.emit(True)
        self.preview.setGeometry(self.scroll.viewport().rect())
        self.preview.show()
        self.preview.raise_()
        self.scroll.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self.timer.start()

    @staticmethod
    def _snapshot(widget):
        # QWidget.grab() forces DrawWindowBackground, which fills transparent
        # containers with the platform's light Window palette during rendering.
        ratio = widget.devicePixelRatioF()
        image = QPixmap(round(widget.width() * ratio), round(widget.height() * ratio))
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        return image

    def _update_target(self):
        top = self.point.y() - self.grab_offset + self.scroll.verticalScrollBar().value()
        target = insertion_index(self.groups, self.source, top, self.base)
        if target == self.target:
            return
        self.target = target
        order = list(self.groups)
        order.insert(target, order.pop(self.source))
        self.animation.stop()
        self.starts = dict(self.positions)
        self.ends = group_positions(order, self.base)
        self.animation.start()

    def _animate(self, progress):
        if self.active:
            self.positions = {key: start + (self.ends[key] - start) * progress for key, start in self.starts.items()}
            self.preview.update()

    def _scroll(self):
        if not self.active or not self.scroll.viewport().rect().contains(self.point):
            return
        delta = edge_scroll_delta(self.point.y(), self.scroll.viewport().height())
        if delta:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.value() + delta)
            self._update_target()
            self.preview.update()

    def paint(self, painter):
        if not self.active:
            return
        painter.fillRect(self.preview.rect(), QColor(PANEL))
        bar = self.scroll.verticalScrollBar().value()
        for block, snapshot in self.backgrounds.items():
            painter.drawPixmap(block.mapTo(self.scroll.viewport(), QPoint()), snapshot)
        left = self.items[0].mapTo(self.scroll.viewport(), QPoint()).x()
        painter.fillRect(QRectF(left, self.base - bar, self.items[0].width(), self.scope_height), QColor(PANEL))
        for i, (block, start, end, height) in enumerate(self.groups):
            if i != self.source:
                x = block.mapTo(self.scroll.viewport(), QPoint()).x()
                painter.drawPixmap(x, round(self.positions[block] - bar), self.images[block])
        block, start, end, height = self.groups[self.source]
        base = self.base - bar
        top = max(base, min(self.point.y() - self.grab_offset, base + self.scope_height - block.height()))
        painter.drawPixmap(block.mapTo(self.scroll.viewport(), QPoint()).x(), round(top), self.images[block])

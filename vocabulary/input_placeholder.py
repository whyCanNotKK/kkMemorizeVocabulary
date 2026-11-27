"""Empty-field hints using the shared Enter icon instead of font glyphs."""
import shiboken6
from PySide6.QtCore import QEvent, QRectF, Qt, Slot
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPalette
from PySide6.QtWidgets import QLineEdit, QStyle, QStyleOptionFrame, QWidget

from .theme import draw_symbol


class EnterPlaceholder(QWidget):
    def __init__(self, field, label, action):
        self.surface = field if isinstance(field, QLineEdit) else field.viewport()
        super().__init__(self.surface)
        self.field = field
        self.prefix, self.suffix = label + '（', ' ' + action + '）'
        self._preedit = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        field.setPlaceholderText('')
        field.setAccessibleDescription(f'{label}（回车 {action}）')
        field.installEventFilter(self)
        if self.surface is not field:
            self.surface.installEventFilter(self)
        field.textChanged.connect(self.refresh)
        self.refresh()

    @Slot()
    def refresh(self):
        # Replacing a comparison card can destroy the viewport before a late
        # text/layout notification arrives. Python references outlive Qt objects.
        if (not shiboken6.isValid(self) or not shiboken6.isValid(self.field)
                or not shiboken6.isValid(self.surface)):
            return
        text = self.field.text() if isinstance(self.field, QLineEdit) else self.field.toPlainText()
        self.setGeometry(self.surface.rect())
        self.setVisible(not text and not self._preedit)
        self.update()

    def eventFilter(self, source, event):
        if source is self.field and event.type() == QEvent.Type.InputMethod:
            self._preedit = bool(event.preeditString())
            self.refresh()
        elif event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange,
                              QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.refresh()
        return False

    def paintEvent(self, event):
        field = self.field
        if isinstance(field, QLineEdit):
            option = QStyleOptionFrame()
            field.initStyleOption(option)
            rect = QRectF(field.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, field))
            margins = field.textMargins()
            rect.adjust(margins.left() + 2, margins.top(), -margins.right() - 2, -margins.bottom())
        else:
            rect = QRectF(self.rect())
            rect.setLeft(field.cursorRect().left())
            rect.setTop(field.cursorRect().top())
            rect.setHeight(field.fontMetrics().height())
        font = QFont(field.font())
        metrics = QFontMetricsF(font)
        icon_size = metrics.height() * .9
        width = metrics.horizontalAdvance(self.prefix + self.suffix) + icon_size
        if width > rect.width() and rect.width() > 0:
            scale = rect.width() / width
            if font.pointSizeF() > 0:
                font.setPointSizeF(font.pointSizeF() * scale)
            else:
                font.setPixelSize(max(1, int(font.pixelSize() * scale)))
            metrics = QFontMetricsF(font)
            icon_size = metrics.height() * .9
        color = field.palette().color(QPalette.ColorRole.PlaceholderText)
        painter = QPainter(self)
        painter.setClipRect(rect)
        painter.setFont(font)
        painter.setPen(color)
        prefix_width = metrics.horizontalAdvance(self.prefix)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.prefix)
        icon_rect = QRectF(rect.left() + prefix_width, rect.center().y() - icon_size / 2, icon_size, icon_size)
        draw_symbol(painter, 'enter_key', icon_rect, color)
        suffix_rect = QRectF(rect)
        suffix_rect.setLeft(icon_rect.right())
        painter.drawText(suffix_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.suffix)

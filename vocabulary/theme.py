from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap, QPolygonF


BG = "#171b22"
PANEL = "#1e242d"
TEXT = "#e8edf4"
MUTED = "#96a1b1"
ACCENT = "#a4ddc5"
GOLD = "#efc578"

STYLESHEET = """
QWidget { color: #e8edf4; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #171b22; }
QFrame#panel { background: #1e242d; border: 1px solid #303945; border-radius: 12px; }
QFrame#conflictCard { background: #1e242d; border: 1px solid #384352; border-radius: 12px; }
QFrame#conflictCard:hover, QFrame#conflictCard:focus { background: #25342f; border-color: #a4ddc5; }
QLabel#title { font-size: 24px; font-weight: 600; }
QLabel#muted { color: #96a1b1; font-size: 12px; }
QLabel#section { font-size: 14px; font-weight: 600; }
QLabel#notice { color: #a4ddc5; }
QLabel#posBadge { color: #bdc8d5; background: #293440; border: 1px solid #465463; border-radius: 5px; padding: 0; }
QFrame#floatingNotice { background: #2c3542; border: none; border-radius: 4px; }
QFrame#floatingNotice QLabel { color: #c9a4a7; background: transparent; border: none; }
QPushButton { background: #2a323f; border: 1px solid #384352; border-radius: 7px; padding: 7px 12px; }
QPushButton:hover { background: #364150; border-color: #738798; }
QPushButton:pressed { background: #202832; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QKeySequenceEdit:focus {
    border: 1px solid #a4ddc5;
}
QPushButton:disabled { color: #626d7c; border-color: #2d3540; background: #222933; }
QPushButton#primary { background: #a4ddc5; color: #152a23; border-color: #a4ddc5; font-weight: 600; }
QPushButton#primary:hover { background: #bcebd6; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #aab7c5; padding: 5px 8px; }
QPushButton#quiet:hover { background: #303a47; }
QPushButton#quiet:focus { border-color: #a4ddc5; }
QPushButton[active="true"] { color: #a4ddc5; background: #2b3b39; border-color: #52796b; }
QLineEdit, QPlainTextEdit, QComboBox, QKeySequenceEdit {
    background: #171d26; border: 1px solid #3c4654; border-radius: 7px; padding: 7px 9px;
    selection-background-color: #3b625b; selection-color: #ffffff;
}
QComboBox { padding-right: 20px; min-height: 20px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; width: 0; }
QComboBox QAbstractItemView { background: #252e39; border: 1px solid #536476; selection-background-color: #3b625b; padding: 5px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QTableView { background: #1e242d; alternate-background-color: #1e242d; border: none;
    gridline-color: #303945; selection-background-color: #2a3542; outline: none; }
QHeaderView::section { background: #1e242d; color: #96a1b1; border: none; border-bottom: 1px solid #36404e;
    padding: 11px 10px; font-size: 12px; text-align: left; }
QTableCornerButton::section { background: #1e242d; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #485362; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #303a47; color: #e8edf4; border: 1px solid #566779; padding: 7px; }
QFrame#wordBlock { border: none; background: transparent; }
QFrame#wordSeparator { border: none; background: #343e4a; }
QMessageBox { background: #1e242d; }
"""


def apply_theme(app):
    # The offscreen Windows plugin has no native font database.
    if app.platformName() == "offscreen":
        from pathlib import Path
        import os
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("msyh.ttc", "segoeui.ttf"):
            if (fonts / filename).exists():
                QFontDatabase.addApplicationFont(str(fonts / filename))
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLESHEET)


def draw_related_arrow(painter: QPainter, x, y):
    painter.save()
    painter.setPen(QPen(QColor("#7f8d9e"), 1.2))
    painter.drawLine(x, y, x, y + 7)
    painter.drawLine(x, y + 7, x + 7, y + 7)
    painter.drawLine(x + 4, y + 4, x + 7, y + 7)
    painter.restore()


def draw_symbol(p: QPainter, name: str, rect: QRectF, color: str):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.center())
    p.scale(rect.width() / 24, rect.height() / 24)
    p.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    if name == "star":
        points = []
        for i in range(10):
            angle, radius = -math.pi / 2 + i * math.pi / 5, (9 if i % 2 == 0 else 4.1)
            points.append(QPointF(math.cos(angle) * radius, math.sin(angle) * radius))
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygonF(points))
    elif name == "circle":
        p.drawEllipse(QRectF(-8, -8, 16, 16))
    elif name == "minus":
        p.setPen(QPen(QColor(color), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(-7, 0), QPointF(7, 0))
    elif name == "trash":
        p.drawLine(QPointF(-8, -6), QPointF(8, -6))
        p.drawLine(QPointF(-3, -9), QPointF(3, -9))
        p.drawPolyline(QPolygonF([QPointF(-6, -6), QPointF(-5, 8), QPointF(5, 8), QPointF(6, -6)]))
        p.drawLine(QPointF(-2, -2), QPointF(-2, 5))
        p.drawLine(QPointF(2, -2), QPointF(2, 5))
    elif name == "edit":
        p.drawPolygon(QPolygonF([QPointF(-7, 7), QPointF(-5, 1), QPointF(5, -9),
                                QPointF(9, -5), QPointF(-1, 5)]))
        p.drawLine(QPointF(3, -7), QPointF(7, -3))
    elif name == "search":
        p.drawEllipse(QRectF(-8, -8, 12, 12))
        p.drawLine(QPointF(2, 2), QPointF(8, 8))
    elif name == "drag":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        for x in (-3, 3):
            for y in (-6, 0, 6):
                p.drawEllipse(QPointF(x, y), 1.4, 1.4)
    elif name == "eye":
        p.drawPolygon(QPolygonF([QPointF(-10, 0), QPointF(-5, -6), QPointF(5, -6),
                                QPointF(10, 0), QPointF(5, 6), QPointF(-5, 6)]))
        p.drawEllipse(QRectF(-3, -3, 6, 6))
    elif name == "refresh":
        p.drawArc(QRectF(-8, -8, 16, 16), 45 * 16, 285 * 16)
        p.drawPolyline(QPolygonF([QPointF(1, -7), QPointF(6, -7), QPointF(6, -12)]))
    elif name == "shuffle":
        for direction in (-1, 1):
            p.drawPolyline(QPolygonF([QPointF(-9, -6 * direction), QPointF(-5, -6 * direction),
                                      QPointF(5, 6 * direction), QPointF(10, 6 * direction)]))
            p.drawPolyline(QPolygonF([QPointF(7, 6 * direction - 3), QPointF(10, 6 * direction),
                                      QPointF(7, 6 * direction + 3)]))
    elif name in {"chevron_down", "chevron_up"}:
        direction = 1 if name == "chevron_down" else -1
        p.setPen(QPen(QColor(color), 2.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(-8, -4 * direction), QPointF(0, 4 * direction),
                                 QPointF(8, -4 * direction)]))
    elif name == "settings":
        p.drawEllipse(QRectF(-7, -7, 14, 14))
        p.drawEllipse(QRectF(-2.5, -2.5, 5, 5))
        for i in range(8):
            angle = i * math.pi / 4
            p.drawLine(QPointF(8 * math.cos(angle), 8 * math.sin(angle)),
                       QPointF(10 * math.cos(angle), 10 * math.sin(angle)))
    elif name == "book":
        p.drawRoundedRect(QRectF(-9, -9, 18, 18), 2, 2)
        p.drawLine(QPointF(-3, -9), QPointF(-3, 9))
        p.drawLine(QPointF(1, -3), QPointF(5, -3))
        p.drawLine(QPointF(1, 1), QPointF(5, 1))
    elif name in {"import", "export"}:
        p.drawPolyline(QPolygonF([QPointF(-8, 3), QPointF(-8, 8), QPointF(8, 8), QPointF(8, 3)]))
        if name == "import":
            p.drawLine(QPointF(0, -9), QPointF(0, 3))
            p.drawPolyline(QPolygonF([QPointF(-4, -1), QPointF(0, 3), QPointF(4, -1)]))
        else:
            p.drawLine(QPointF(0, 3), QPointF(0, -9))
            p.drawPolyline(QPolygonF([QPointF(-4, -5), QPointF(0, -9), QPointF(4, -5)]))
    p.restore()


@lru_cache(maxsize=64)
def icon(name: str, color: str = MUTED) -> QIcon:
    wide = name in {"chevron_down", "chevron_up"}
    pixmap = QPixmap(80 if wide else 48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    draw_symbol(p, name, QRectF(4, -12, 72, 72) if wide else QRectF(4, 4, 40, 40), color)
    p.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


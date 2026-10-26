"""Render the application's own vector mark into a Windows multi-size icon."""
import os
from pathlib import Path
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from vocabulary.theme import draw_symbol


def main():
    app = QApplication.instance() or QApplication([])
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#253d36"))
        painter.drawRoundedRect(QRectF(0, 0, size, size), size * .22, size * .22)
        draw_symbol(painter, "book", QRectF(size * .2, size * .2, size * .6, size * .6), "#a4ddc5")
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        images.append((size, bytes(buffer.data())))
    offset = 6 + len(images) * 16
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, payload in images:
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    output = Path(__file__).resolve().parents[1] / "assets" / "app.ico"
    output.parent.mkdir(exist_ok=True)
    output.write_bytes(bytes(directory) + b"".join(payload for _, payload in images))


if __name__ == "__main__":
    main()

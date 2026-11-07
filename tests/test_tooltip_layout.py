import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QPainter, QTextDocument

from vocabulary.theme import copy_word_tooltip, study_mark_tooltip


@pytest.mark.parametrize("tooltip", [copy_word_tooltip, study_mark_tooltip])
def test_tooltip_icon_and_text_have_the_same_visible_center(qapp, tooltip):
    document = QTextDocument()
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    document.setDefaultFont(font)
    document.setHtml(tooltip())
    document.adjustSize()
    block = document.begin()
    layout = block.layout()
    assert layout.lineCount() == 1
    line = layout.lineAt(0)
    icon_position = block.text().index("\ufffc")

    scale = 3
    size = document.size()
    image = QImage(round(size.width() * scale), round(size.height() * scale), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.scale(scale, scale)
    document.drawContents(painter)
    painter.end()

    def visible_center(start, end):
        left = round((layout.position().x() + line.cursorToX(start)[0]) * scale)
        right = round((layout.position().x() + line.cursorToX(end)[0]) * scale)
        rows = [y for y in range(image.height())
                if any(image.pixelColor(x, y).alpha() >= 96 for x in range(left, right))]
        return (min(rows) + max(rows)) / (2 * scale)

    text_center = visible_center(0, 2)
    icon_center = visible_center(icon_position, icon_position + 1)
    assert icon_center == pytest.approx(text_center, abs=1)

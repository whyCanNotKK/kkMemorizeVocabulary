import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QPainter, QTextDocument

from vocabulary.theme import copy_word_tooltip, mouse_action_hint, study_mark_tooltip, tooltip_lines


@pytest.mark.parametrize("tooltip", [copy_word_tooltip, study_mark_tooltip,
                                    lambda: study_mark_tooltip(hidden=True),
                                    lambda: tooltip_lines(mouse_action_hint('复制单词拼写', 'left'),
                                                          mouse_action_hint('编辑拼写和音标', 'right')),
                                    lambda: tooltip_lines('按住拖动改变释义排序',
                                                          mouse_action_hint('编辑', 'right')),
                                    lambda: tooltip_lines(mouse_action_hint('编辑', 'right', 'F2'),
                                                          mouse_action_hint('隐藏释义', 'left', '空格', modifier='Ctrl'))])
def test_tooltip_icon_and_text_have_the_same_visible_center(themed_app, tooltip):
    document = QTextDocument()
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    document.setDefaultFont(font)
    document.setHtml(tooltip())
    document.adjustSize()
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
        top = round(layout.position().y() * scale)
        bottom = min(image.height(), round((layout.position().y() + line.height()) * scale))
        rows = [y for y in range(top, bottom)
                if any(image.pixelColor(x, y).alpha() >= 96 for x in range(left, right))]
        return (min(rows) + max(rows)) / (2 * scale)

    assert document.blockCount() == (1 if tooltip is copy_word_tooltip else 2)
    block = document.begin()
    while block.isValid():
        layout = block.layout()
        assert layout.lineCount() == 1
        line = layout.lineAt(0)
        text_center = visible_center(0, 2)
        for position, character in enumerate(block.text()):
            if character == '\ufffc':
                assert visible_center(position, position + 1) == pytest.approx(text_center, abs=1)
        block = block.next()

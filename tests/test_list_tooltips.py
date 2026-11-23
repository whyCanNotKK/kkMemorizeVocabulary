from datetime import date

import pytest
from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent, QTextDocument
from PySide6.QtWidgets import QApplication, QToolTip

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.mark.parametrize('source', ['list', 'button'])
def test_tooltip_expires_after_five_seconds_without_input(window, repo, qtbot, source):
    entry = repo.save(WordEntry('word', [Sense('n.', 'meaning')]))
    window.refresh()
    table = window.table
    qtbot.wait(20)
    QToolTip.hideText()
    qtbot.waitUntil(lambda: not QToolTip.isVisible())
    if source == 'list':
        index = table._entry_index(entry.id, column=2)
        widget = table.viewport()
        point = table.delegate.mask_rect(table.visualRect(index)).center().toPoint()
    else:
        widget = window.import_button
        point = widget.rect().center()
    assert (table if source == 'list' else widget).toolTipDuration() == 5000
    QApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, point, widget.mapToGlobal(point)))
    qtbot.waitUntil(QToolTip.isVisible)
    timer = QElapsedTimer()
    timer.start()
    try:
        qtbot.wait(4000)
        assert QToolTip.isVisible()
        qtbot.waitUntil(lambda: not QToolTip.isVisible(), timeout=2000)
        assert timer.elapsed() <= 6000
    finally:
        QToolTip.hideText()


@pytest.mark.parametrize('page,action', [
    ('pending', 'global'), ('pending', 'space'), ('pending', 'mouse'),
    ('study17', 'global'), ('study17', 'pos'), ('study17', 'mouse'),
])
def test_revealing_dismisses_visible_mask_tooltip_without_mouse_movement(
        window, repo, qtbot, monkeypatch, page, action):
    entry = repo.save(WordEntry('word', [Sense('n.', 'meaning')],
                               [RelatedWord('child', [Sense('n.', 'child meaning')])]))
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter(page)
    table = window.current_table()
    table.select_entry(entry.id)
    table.setFocus()
    qtbot.wait(20)
    index = table._entry_index(entry.id, column=2)
    point = table.delegate.mask_rect(table.visualRect(index)).center().toPoint()
    QApplication.sendEvent(table.viewport(), QHelpEvent(
        QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))
    qtbot.waitUntil(QToolTip.isVisible)
    assert '查看释义和关联词' in QToolTip.text()
    try:
        if action == 'global':
            window.set_global_hidden(False)
        elif action == 'mouse':
            qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        else:
            qtbot.keyClick(table, Qt.Key.Key_N if action == 'pos' else Qt.Key.Key_Space)
        assert not table.model().rows[0].hidden
        qtbot.waitUntil(lambda: not QToolTip.isVisible(), timeout=1000)
    finally:
        QToolTip.hideText()


@pytest.fixture(params=['pending', 'all', 'learned', 'study17'])
def tooltip_list(window, repo, monkeypatch, request):
    entry = repo.save(WordEntry('word', [Sense('n.', 'meaning'), Sense('v.', 'another')],
                               [RelatedWord('child', [Sense('n.', 'child meaning')])],
                               learned=request.param == 'learned'))
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter(request.param)
    table = window.current_table()
    tips = []
    monkeypatch.setattr('PySide6.QtWidgets.QToolTip.showText', lambda pos, text, *args: tips.append(text))

    def hover(index, point=None):
        tips.clear()
        point = point or table.visualRect(index).center()
        QApplication.sendEvent(table.viewport(), QHelpEvent(
            QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))
        if not tips:
            return '', ''
        document = QTextDocument()
        document.setHtml(tips[-1])
        return document.toPlainText(), tips[-1]

    return table, entry, hover


def test_mask_word_pos_translation_and_related_tooltips_use_matching_hit_regions(tooltip_list):
    table, entry, hover = tooltip_list
    index = table._entry_index(entry.id, column=2)
    plain, html = hover(index, table.delegate.mask_rect(table.visualRect(index)).center().toPoint())
    assert plain == '查看释义和关联词 · \ufffc · \ufffc · ' + ('输入词性' if table.study_mode else '空格')
    assert 'alt="鼠标左键"' in html and 'alt="鼠标右键"' in html
    table.set_entry_revealed(entry.id, True)
    for owner in (-1, 0):
        index = table._entry_index(entry.id, owner=owner)
        plain, html = hover(index)
        assert plain.splitlines() == ['复制单词拼写 · \ufffc', '编辑拼写和音标 · \ufffc']
        assert 'alt="鼠标左键"' in html and 'alt="鼠标右键"' in html
    index = table._entry_index(entry.id, sense=1)
    assert hover(index)[0] == ''  # The repeated blank name cell does not copy spelling.
    index = table._entry_index(entry.id, column=2)
    assert hover(index, table.delegate.pos_rect(index, table.visualRect(index)).center().toPoint())[0].splitlines() == [
        '按住拖动改变释义排序', '编辑 · \ufffc']
    index = table._entry_index(entry.id, column=3)
    rect = table.visualRect(index)
    point = rect.topLeft() + QPoint(15, 15)
    translation_actions = ['编辑 · \ufffc · F2', '隐藏释义 · Ctrl + \ufffc · 空格']
    assert hover(index, point)[0].splitlines() == ['复制 · \ufffc', *translation_actions]
    assert hover(index, rect.center())[0].splitlines() == translation_actions
    empty = table._entry_index(entry.id, sense=1, column=3)
    empty.data(Qt.ItemDataRole.UserRole).word.senses[1].translation = ''
    assert hover(empty)[0].splitlines() == translation_actions
    index = table._entry_index(entry.id, owner=0)
    point = table.visualRect(index).topLeft() + QPoint(10, 15)
    assert hover(index, point)[0].splitlines() == ['按住拖动改变关联词排序', '整词复制 · \ufffc']


def test_star_drag_and_edit_icon_follow_page_and_current_shortcut(tooltip_list, window):
    table, entry, hover = tooltip_list
    index = table._entry_index(entry.id, column=0)
    assert hover(index)[0] == ('' if table.study_mode else '取消重点 · F1 · Ctrl+I\n整词复制 · \ufffc')
    index.data(Qt.ItemDataRole.UserRole).entry.important = False
    assert hover(index)[0] == ('' if table.study_mode else '设置为重点 · F1 · Ctrl+I\n整词复制 · \ufffc')
    window.shortcuts.update(dict(window.shortcuts.values, important='Alt+I'))
    assert hover(index)[0] == ('' if table.study_mode else '设置为重点 · F1 · Alt+I\n整词复制 · \ufffc')
    index = table._entry_index(entry.id, column=table.action_column)
    actions = dict(table.action_rects(table.visualRect(index), index.data(Qt.ItemDataRole.UserRole)))
    if 'trash' in actions:
        assert hover(index, actions['trash'].center())[0] == '删除单词 · Del'
    if 'learned' in actions:
        assert hover(index, actions['learned'].center())[0] == (
            '标记为"待记" · F4' if entry.learned else '标记为"记下" · F4')
    if 'drag' in actions:
        assert hover(index, actions['drag'].center())[0] == '按住拖动改变单词排序'
    if 'edit' in actions:
        assert hover(index, actions['edit'].center())[0] == '编辑单词 · ' + window.shortcuts.labels()['edit_selected']
        window.shortcuts.update(dict(window.shortcuts.values, edit_selected='Ctrl+Shift+E'))
        assert hover(index, actions['edit'].center())[0] == '编辑单词 · Ctrl+Shift+E'

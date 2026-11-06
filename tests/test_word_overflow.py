from datetime import date
import math

import pytest
from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QHelpEvent, QTextDocument
from PySide6.QtWidgets import QApplication, QToolTip

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.table import word_tooltip_markup


@pytest.fixture(params=['pending', 'all', 'learned', 'study17'])
def overflow(window, repo, monkeypatch, request):
    entry = repo.save(WordEntry('vocabulary', [Sense('n.', 'first'), Sense('v.', 'second')],
                               [RelatedWord('vocabularies', [Sense('n.', 'child')], '/vəˈkæbjələriz/')],
                               learned=request.param == 'learned', phonetic='/vəˈkæbjələri/'))
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    window.set_filter(request.param)
    table = window.current_table()
    table.set_entry_revealed(entry.id, True)
    table.select_entry(entry.id)
    return table, entry


@pytest.mark.parametrize('owner', [-1, 0])
def test_width_strategies_change_for_same_cell_without_repopulating(overflow, owner):
    table, entry = overflow
    index = table._entry_index(entry.id, owner=owner)
    row = index.data(Qt.ItemDataRole.UserRole)
    delegate = table.delegate
    model = table.model()
    identity = model.rows
    word, ipa, tip = delegate.name_layout(index, QRectF(0, 0, 600, 40), table.font())
    word_width = QFontMetricsF(word[0]).horizontalAdvance(row.word.text)
    ipa_width = QFontMetricsF(ipa[0]).horizontalAdvance(row.word.phonetic)
    ellipsis = QFontMetricsF(ipa[0]).horizontalAdvance('…')
    margins = 36 if owner >= 0 else 20
    cases = [
        (margins + word_width - 12, 'word'),
        (margins + word_width + 8 + ellipsis - 2, 'no_ipa'),
        (margins + word_width + 8 + (ellipsis + ipa_width) / 2, 'ipa'),
        (margins + word_width + 8 + ipa_width + 2, 'full'),
    ]
    for width, strategy in cases + list(reversed(cases)):
        table.setColumnWidth(1, math.floor(width))
        rect = table.visualRect(index)
        word, ipa, tip = delegate.name_layout(index, rect, table.font())
        assert model.rows is identity
        assert delegate.word_tooltip(index) == tip
        if strategy == 'word':
            assert word[1].endswith('…') and ipa is None
            assert tip == row.word.text + '    ' + row.word.phonetic
        else:
            assert word[1] == row.word.text
            assert tip == ('' if strategy == 'full' else row.word.phonetic)
            if strategy == 'no_ipa':
                assert ipa is None
            else:
                assert ipa[1] == row.word.phonetic if strategy == 'full' else ipa[1].endswith('…')
                assert word[2].right() + 7.9 < ipa[2].left()


@pytest.mark.parametrize('hidden', [False, True])
def test_tooltip_actual_event_preserves_full_content_and_four_spaces(overflow, monkeypatch, hidden):
    table, entry = overflow
    table.set_entry_revealed(entry.id, not hidden)
    index = table._entry_index(entry.id)
    table.setColumnWidth(1, 60)
    shown = []
    monkeypatch.setattr('PySide6.QtWidgets.QToolTip.showText', lambda pos, text, *args: shown.append(text))
    point = table.visualRect(index).center()
    QApplication.sendEvent(table.viewport(), QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))
    document = QTextDocument()
    document.setHtml(shown[-1])
    assert document.toPlainText().replace('\u00a0', ' ') == entry.text + '    ' + entry.phonetic
    table.setColumnWidth(1, 500)
    shown.clear()
    QApplication.sendEvent(table.viewport(), QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))
    assert not shown


def test_word_without_phonetic_and_repeated_sense_cells(overflow):
    table, entry = overflow
    index = table._entry_index(entry.id)
    row = index.data(Qt.ItemDataRole.UserRole)
    row.entry.phonetic = ''
    table.setColumnWidth(1, 50)
    assert table.delegate.word_tooltip(index) == entry.text
    assert table.delegate.phonetic_layout(index, table.visualRect(index), table.font()) is None
    table.setColumnWidth(1, 300)
    assert not table.delegate.word_tooltip(index)
    repeated = table._entry_index(entry.id, sense=1)
    assert not table.delegate.word_tooltip(repeated)


def test_actual_tooltip_uses_one_line_at_natural_content_width(overflow, qtbot):
    table, entry = overflow
    table.setColumnWidth(1, 60)
    index = table._entry_index(entry.id)
    point = table.visualRect(index).center()
    QApplication.sendEvent(table.viewport(), QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)))
    qtbot.waitUntil(QToolTip.isVisible)
    tip = next(widget for widget in QApplication.topLevelWidgets()
               if widget.metaObject().className() == 'QTipLabel' and widget.isVisible())
    text = entry.text + '    ' + entry.phonetic
    content_width = QFontMetricsF(tip.font()).horizontalAdvance(text)
    assert content_width <= tip.width() <= math.ceil(content_width) + 40
    assert tip.height() <= QFontMetricsF(tip.font()).height() + 40
    document = QTextDocument()
    document.setHtml(QToolTip.text())
    assert document.toPlainText().replace('\u00a0', ' ') == text
    QToolTip.hideText()


def test_over_screen_tooltip_wraps_without_losing_content(overflow, qtbot):
    table, entry = overflow
    text = 'verylongword' * 20 + '    /long transcription/'
    maximum = table.screen().availableGeometry().width() - 48
    markup = word_tooltip_markup(text, table.font(), maximum)
    document = QTextDocument()
    document.setHtml(markup)
    plain = document.toPlainText().replace('\u00a0', ' ')
    assert plain.replace('\n', '') == text
    for line in plain.splitlines():
        assert QFontMetricsF(table.font()).horizontalAdvance(line) <= maximum
    QToolTip.showText(table.mapToGlobal(table.rect().center()), markup, table)
    qtbot.waitUntil(QToolTip.isVisible)
    tip = next(widget for widget in QApplication.topLevelWidgets()
               if widget.metaObject().className() == 'QTipLabel' and widget.isVisible())
    assert tip.width() <= table.screen().availableGeometry().width()
    assert tip.height() > QFontMetricsF(tip.font()).height() + 40
    QToolTip.hideText()

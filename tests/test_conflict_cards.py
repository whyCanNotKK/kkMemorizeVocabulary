from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QRadioButton

from vocabulary.domain import POS_OPTIONS, RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ConflictCard, ElidedLabel, ImportConflictDialog, RelatedArrow
from vocabulary.theme import ACCENT


def by_text(repo, text):
    return next(entry for entry in repo.list_entries() if entry.text.casefold() == text.casefold())


def dialog_with_words(repo, qtbot, count=20):
    originals = [repo.save(WordEntry(f"word-{i}", [Sense("n.", "旧释义\n原来的第二行")]))
                 for i in range(count)]
    incoming = [WordEntry(f"WORD-{i}", [Sense("v.", "新释义\n新第二行")], important=False, learned=True)
                for i in range(count)]
    result = repo.import_entries(incoming)
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(30)
    return dialog, originals, incoming


@pytest.mark.parametrize("area", ["background", "word", "description", "star", "learned", "pos_badge"])
def test_whole_card_including_child_labels_commits_and_removes_pair(repo, qtbot, area):
    dialog, originals, incoming = dialog_with_words(repo, qtbot, 2)
    card = dialog.cards[0][1]
    assert not dialog.findChildren(QRadioButton)
    if area == "background":
        qtbot.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(card.width() - 5, card.height() - 5))
    elif area == "pos_badge":
        qtbot.mouseClick(card.description.findChild(QLabel, "posBadge"), Qt.MouseButton.LeftButton)
    else:
        qtbot.mouseClick(getattr(card, area), Qt.MouseButton.LeftButton)
    adopted = by_text(repo, incoming[0].text)
    assert adopted.id != originals[0].id
    assert adopted == replace(incoming[0], id=adopted.id, sort_order=1)
    assert len(dialog.conflicts) == 1 and len(dialog.cards) == 1
    assert dialog.cards[0][0].entry.id == originals[1].id
    assert dialog.isVisible()
    dialog.reject()
    assert by_text(repo, incoming[0].text).learned
    assert repo.get(originals[1].id) == originals[1]


def test_comparison_labels_alignment_and_live_counts(repo, qtbot, themed_app, tmp_path):
    for name in ('one', 'two'):
        repo.save(WordEntry(name, [Sense('n.', '旧')]))
    result = repo.import_entries([WordEntry(name, [Sense('v.', '新')]) for name in ('one', 'two', 'new')])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.resize(720, 480)
    dialog.show()
    qtbot.wait(20)
    assert dialog.windowTitle() == '导入重复词对比选取'
    assert dialog.summary.accessibleName() == '导入新词：1 个，重复单词：2 个，点击卡片立即采用'
    for count in (1, 2):
        assert f'<span style="color:{ACCENT}">{count}</span>' in dialog.summary.text()
    assert 'font-size' not in dialog.summary.text()
    assert dialog.summary.alignment() == Qt.AlignmentFlag.AlignCenter
    assert {button.text() for button in dialog.findChildren(QPushButton)} == {'全部选用'}
    assert [label.text() for label in dialog.column_titles] == ['本地单词', '导入单词']
    for index, (title, button) in enumerate(zip(dialog.column_titles,
                                              (dialog.all_left_button, dialog.all_right_button))):
        assert title.alignment() == Qt.AlignmentFlag.AlignCenter
        assert title.parentWidget() is button.parentWidget()
        assert title.geometry().right() < button.geometry().left()
        assert abs(title.geometry().center().y() - button.geometry().center().y()) <= 2
        center = title.parentWidget().mapTo(dialog, title.parentWidget().rect().center()).x()
        expected = dialog.width() * (0.25 if index == 0 else 0.75)
        assert abs(center - expected) <= 10
    dialog.grab().save(str(tmp_path / 'comparison-header.png'))
    qtbot.mouseClick(dialog.cards[0][0], Qt.MouseButton.LeftButton)
    assert dialog.summary.accessibleName() == '导入新词：1 个，重复单词：1 个，点击卡片立即采用'
    dialog.close()
    assert not dialog.isVisible() and repo.list_entries()[0].senses[0].translation == '旧'


@pytest.mark.parametrize("incoming", [False, True])
def test_bulk_choice_immediately_commits_remaining_and_finishes(repo, qtbot, incoming):
    dialog, originals, imported = dialog_with_words(repo, qtbot, 3)
    qtbot.mouseClick(dialog.all_right_button if incoming else dialog.all_left_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted and not dialog.conflicts
    for original, candidate in zip(originals, imported):
        actual = by_text(repo, original.text)
        assert actual == (replace(candidate, id=actual.id, sort_order=actual.sort_order) if incoming else original)
        if incoming:
            assert actual.id != original.id


def test_card_removal_preserves_pixel_scroll_and_clamps_at_bottom(repo, qtbot):
    dialog, originals, imported = dialog_with_words(repo, qtbot)
    bar = dialog.scroll.verticalScrollBar()
    position = min(400, bar.maximum() // 2)
    assert position > 0
    bar.setValue(position)
    qtbot.wait(20)
    # Click an actual card label in the middle of the scrolled viewport.
    target = next(right for left, right in dialog.cards
                  if 50 < right.mapTo(dialog.scroll.viewport(), QPoint(0, 0)).y() < 180)
    target_id = target.entry.text
    qtbot.mouseClick(target.description, Qt.MouseButton.LeftButton)
    qtbot.wait(30)
    assert bar.value() == position
    assert len(dialog.conflicts) == 19
    assert not any(right.entry.text == target_id for left, right in dialog.cards)
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    bar.setValue(bar.maximum())
    qtbot.mouseClick(dialog.cards[-1][0], Qt.MouseButton.LeftButton)
    qtbot.wait(30)
    assert bar.value() == bar.maximum()


def test_failed_batch_retains_cards_and_previously_committed_choices(repo, qtbot, monkeypatch):
    dialog, originals, imported = dialog_with_words(repo, qtbot, 3)
    qtbot.mouseClick(dialog.cards[0][1], Qt.MouseButton.LeftButton)
    dialog.conflicts[-1].incoming.senses[0].translation = "FAIL"
    repo.connection.execute("""CREATE TRIGGER fail_sense BEFORE INSERT ON senses
        WHEN NEW.translation='FAIL' BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[2]))
    qtbot.mouseClick(dialog.all_right_button, Qt.MouseButton.LeftButton)
    assert len(dialog.conflicts) == 2 and dialog.isVisible() and messages
    assert by_text(repo, imported[0].text).learned
    assert repo.get(originals[1].id) == originals[1]
    assert repo.get(originals[2].id) == originals[2]


def test_same_word_remaining_left_cards_update_to_current_system_entry(repo, qtbot):
    original = repo.save(WordEntry("same", [Sense("n.", "原词")]))
    result = repo.import_entries([WordEntry("SAME", [Sense("v.", "第一次覆盖")], learned=True),
                                  WordEntry("Same", [Sense("adj.", "第二次候选")])])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.cards[0][1], Qt.MouseButton.LeftButton)
    assert dialog.cards[0][0].entry == by_text(repo, "same")
    assert dialog.cards[0][0].entry.id != original.id
    assert "第一次覆盖" in dialog.cards[0][0].description.text()
    qtbot.mouseClick(dialog.cards[0][0], Qt.MouseButton.LeftButton)
    assert by_text(repo, "same").senses[0].translation == "第一次覆盖"


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Space])
def test_keyboard_can_choose_card(repo, qtbot, key):
    dialog, originals, imported = dialog_with_words(repo, qtbot, 1)
    right = dialog.cards[0][1]
    right.setFocus()
    qtbot.keyClick(right, key)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert by_text(repo, imported[0].text).learned


def test_phonetics_use_separate_aligned_lines_and_cards_remain_clickable(repo, qtbot, tmp_path, themed_app):
    original = repo.save(WordEntry('record', [Sense('n.', '记录'), Sense('v.', '记录下来')],
        [RelatedWord('recorder', [Sense('n.', '录音机')], phonetic='child'),
         RelatedWord('silent', [Sense('adj.', '无声的')])], phonetic='/old/'))
    incoming = replace(original, phonetic='/new/')
    dialog = ImportConflictDialog(repo, repo.import_entries([incoming]))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(30)
    for card, phonetic in zip(dialog.cards[0], ['old', 'new']):
        assert card.word.text() == 'record'
        labels = card.description.findChildren(QLabel)
        assert [label.text() for label in labels if label.text().startswith('recorder')] == ['recorder']
        assert any(label.text() == 'silent' for label in labels)
        for word_text, phonetic_text in [('record', phonetic), ('recorder', 'child')]:
            name = next(label for label in labels if label.text() == word_text)
            sound = next(label for label in labels if label.text() == phonetic_text)
            assert name.mapTo(card, QPoint()).x() == sound.mapTo(card, QPoint()).x()
            assert name.mapTo(card, QPoint()).y() + name.height() <= sound.mapTo(card, QPoint()).y()
        assert card.description.lines.count() == 11  # Three name cells, four POS/translation pairs.
        assert len(card.findChildren(RelatedArrow)) == 2
        grid = card.description.lines
        assert grid.getItemPosition(grid.indexOf(card.word.parentWidget())) == (0, 0, 2, 1)
        meaning = next(label for label in labels if label.text() == '记录')
        assert meaning.width() > card.word.parentWidget().width()
        assert meaning.mapTo(card, QPoint()).x() > card.word.mapTo(card, QPoint()).x() + card.word.width()
        assert abs(meaning.mapTo(card, QPoint()).y() - card.word.mapTo(card, QPoint()).y()) <= 3
        child = next(label for label in labels if label.text().startswith('recorder'))
        child_meaning = next(label for label in labels if label.text() == '录音机')
        assert abs(child.mapTo(card, QPoint()).y() - child_meaning.mapTo(card, QPoint()).y()) <= 3
        content_bottom = max(label.mapTo(card, QPoint(0, label.height())).y() for label in labels)
        assert card.height() - content_bottom <= 20
    assert dialog.height() < 400
    assert dialog.scroll.verticalScrollBar().maximum() == 0
    dialog.grab().save(str(tmp_path / 'inline-phonetics.png'))
    child = next(label for label in dialog.cards[0][1].description.findChildren(QLabel)
                 if label.text().startswith('recorder'))
    qtbot.mouseClick(child, Qt.MouseButton.LeftButton)
    assert by_text(repo, 'record').phonetic == '/new/'
    assert not dialog.conflicts


def test_floating_status_icons_do_not_change_content_geometry(qtbot, themed_app):
    entry = WordEntry('longer-word', [Sense('n.', '释义')], phonetic='/sound/')
    card = ConflictCard(entry, 'left')
    qtbot.addWidget(card)
    card.resize(440, 100)
    card.show()
    qtbot.wait(20)
    before = card.description.geometry()
    word_width = card.word.width()
    assert card.description.x() <= 15
    assert card.status_badges.y() == 0
    assert card.status_badges.geometry().right() < card.width()
    assert card.status_badges.geometry().bottom() < card.description.y()
    assert card.learned.pixmap().toImage().pixelColor(6, 6).alpha() == 0
    card.set_entry(replace(entry, important=False, learned=True))
    qtbot.wait(20)
    assert card.description.geometry() == before and card.word.width() == word_width
    assert card.learned.pixmap().toImage().pixelColor(6, 6).green() > 150
    assert card.star.toolTip() == '非重点' and card.learned.toolTip() == '已记住'
    card.resize(600, 100)
    qtbot.wait(10)
    assert card.status_badges.x() == card.width() - card.status_badges.width() - 14


def test_all_parts_of_speech_render_translations_at_the_same_start(qtbot):
    entry = WordEntry("alignment", [Sense(pos, f"主译文-{index}") for index, (pos, _) in enumerate(POS_OPTIONS)],
                      [RelatedWord("related", [Sense(pos, f"关联译文-{index}")
                                               for index, (pos, _) in enumerate(POS_OPTIONS)])])
    entry.senses[0].translation += "\n续行译文"
    entry.senses[1].translation = "主译文-长内容" + "需要自动换行的翻译" * 15
    card = ConflictCard(entry, "right")
    qtbot.addWidget(card)
    card.resize(360, 1100)
    card.show()
    qtbot.wait(30)
    labels = card.description.findChildren(QLabel)
    main = [label for label in labels if label.text().startswith("主译文-")]
    related = [label for label in labels if label.text().startswith("关联译文-")]
    assert len(main) == len(related) == len(POS_OPTIONS)
    main_x = {label.mapTo(card, QPoint()).x() for label in main}
    related_x = {label.mapTo(card, QPoint()).x() for label in related}
    assert len(main_x) == len(related_x) == 1
    badges = [label for label in labels if label.objectName() == "posBadge"]
    assert len(badges) == 2 * len(POS_OPTIONS)
    assert len({(label.width(), label.height()) for label in badges}) == 1
    assert all(label.alignment() == Qt.AlignmentFlag.AlignCenter for label in badges)
    assert all(label.text() in {pos for pos, _ in POS_OPTIONS} for label in badges)
    assert related_x == main_x
    continuation = next(label for label in labels if "\n续行译文" in label.text())
    assert continuation.mapTo(card, QPoint()).x() in main_x
    assert not any("--" in label.text() for label in labels)
    long_translation = next(label for label in main if label.text().startswith("主译文-长内容"))
    assert long_translation.height() == long_translation.fontMetrics().height() + 4
    assert long_translation.display_text.endswith('...')
    assert long_translation.toolTip() == long_translation.text()


@pytest.mark.parametrize('text', ['long-word-' * 8, '/long-phonetic-' * 8 + '/',
                                 '很长的释义' * 20, '第一行\n第二行'])
def test_cell_elision_and_full_tooltip_follow_available_width(qtbot, themed_app, text):
    label = ElidedLabel(text)
    qtbot.addWidget(label)
    label.resize(70, label.height())
    label.show()
    assert label.text() == text
    assert label.toolTip() == text
    if '\n' not in text:
        assert label.display_text.endswith('...')
        assert label.fontMetrics().horizontalAdvance(label.display_text) <= label.contentsRect().width()
    label.resize(label.fontMetrics().horizontalAdvance(text.replace('\n', ' ')) + 30, label.height())
    qtbot.wait(10)
    assert label.display_text == text.replace('\n', ' ')
    assert label.toolTip() == (text if '\n' in text else '')

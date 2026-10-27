from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QRadioButton

from vocabulary.domain import POS_OPTIONS, RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ConflictCard, ImportConflictDialog


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
    bar.setValue(400)
    qtbot.wait(20)
    # Click an actual card label in the middle of the scrolled viewport.
    target = next(right for left, right in dialog.cards
                  if 50 < right.mapTo(dialog.scroll.viewport(), QPoint(0, 0)).y() < 180)
    target_id = target.entry.text
    qtbot.mouseClick(target.description, Qt.MouseButton.LeftButton)
    qtbot.wait(30)
    assert bar.value() == 400
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
    assert next(iter(related_x)) - next(iter(main_x)) == card.description.fontMetrics().horizontalAdvance("    ")
    continuation = next(label for label in labels if "\n续行译文" in label.text())
    assert continuation.mapTo(card, QPoint()).x() in main_x
    assert not any("--" in label.text() for label in labels)
    long_translation = next(label for label in main if label.text().startswith("主译文-长内容"))
    assert long_translation.height() > long_translation.fontMetrics().height()

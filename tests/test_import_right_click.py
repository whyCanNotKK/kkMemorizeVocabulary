"""Adoption and local synchronizations use different mouse buttons and durable targets."""
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent, QTextDocument
from PySide6.QtWidgets import QApplication, QMessageBox, QToolTip

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ImportConflictDialog, RelatedArrow
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_word_or_study_file
from vocabulary.transfer import ImportSource, write_entries


def plain(html):
    document = QTextDocument()
    document.setHtml(html)
    return document.toPlainText()


def comparison(repo, qtbot, tmp_path):
    original = repo.save(WordEntry('main', [Sense('n.', '第一条'), Sense('n.', '第二条'), Sense('v.', '动词')],
        [RelatedWord('b', [Sense('n.', '乙')]),
         RelatedWord('A', [Sense('n.', '甲一'), Sense('n.', '甲二')], 'old'),
         RelatedWord('a', [Sense('n.', '另一个同名关联词')], 'untouched'),
         RelatedWord('c', [Sense('n.', '丙')])], important=False, phonetic='old'))
    candidate = WordEntry('MAIN', [Sense('n.', '导入一'), Sense('n.', '导入二'),
                                 Sense('v.', ''), Sense('adj.', '新词性')],
        [RelatedWord('a', [Sense('n.', '导入甲'), Sense('adv.', '甲副词')], '/new/'),
         RelatedWord('d', [Sense('n.', '丁'), Sense('v.', '丁动词')], '/d/')],
        important=False, phonetic='/new/')
    path = tmp_path / 'source.json'
    write_entries(path, [candidate, replace(candidate, text='Main')])
    source, entries = ImportSource.read(path, parse_word_or_study_file)
    dialog = ImportConflictDialog(repo, repo.import_entries(entries), source=source)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(20)
    return dialog, original, path


def test_translation_sync_uses_first_pos_and_first_related_without_rewriting_others(repo, qtbot, tmp_path):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    repo.prepare_study(StudyContext(date(2026, 11, 22), False))
    progress = repo.study_backup().records
    history = repo.study_task_histories()
    metadata = tuple(repo.connection.execute(
        'SELECT id,created_at,sort_order,important,learned FROM words WHERE id=?', (original.id,)).fetchone())
    identities = [tuple(row) for row in repo.connection.execute('SELECT id,word_id,related_id,position FROM senses ORDER BY id')]
    before_file = path.read_bytes()
    right = dialog.cards[0][1]
    # Both the POS badge and the translation (including an empty cell) are targets.
    for owner, sense, part in [(-1, 0, 0), (-1, 1, 1), (-1, 2, 1), (-1, 3, 0), (0, 0, 1), (0, 1, 0)]:
        qtbot.mouseClick(right.description.sense_labels[owner, sense][part], Qt.MouseButton.RightButton)
        assert len(dialog.conflicts) == 2
    qtbot.mouseClick(right.description.phonetic_labels[0], Qt.MouseButton.RightButton)
    expected = replace(original,
        senses=[Sense('n.', '导入二'), original.senses[1], Sense('v.', ''), Sense('adj.', '新词性')],
        related=[original.related[0], replace(original.related[1], phonetic='new',
            senses=[Sense('n.', '导入甲'), original.related[1].senses[1], Sense('adv.', '甲副词')]),
            *original.related[2:]])
    assert repo.get(original.id) == expected
    assert all(left.entry == expected for left, _ in dialog.cards)
    assert metadata == tuple(repo.connection.execute(
        'SELECT id,created_at,sort_order,important,learned FROM words WHERE id=?', (original.id,)).fetchone())
    after = [tuple(row) for row in repo.connection.execute('SELECT id,word_id,related_id,position FROM senses ORDER BY id')]
    assert after[:len(identities)] == identities
    assert repo.study_backup().records == progress and repo.study_task_histories() == history
    assert path.read_bytes() == before_file and not dialog.replaced_names
    dialog.reject()
    reopened = Repository(repo.path)
    try:
        assert reopened.get(original.id) == expected
    finally:
        reopened.close()


def test_empty_new_pos_is_kept_and_related_append_rechecks_current_database(repo):
    original = repo.save(WordEntry('same', [Sense('n.', '原释义')]))
    saved = repo.sync_import_translation(original.id, Sense('adv.', ''))
    assert saved.senses == original.senses + [Sense('adv.', '')]
    # A related word may have the same spelling as its owning main word.
    related = RelatedWord('same', [Sense('n.', '关联一'), Sense('v.', '关联二')], 'sound')
    added = repo.append_import_related(original.id, related)
    assert added.related == [related]
    changes = repo.connection.total_changes
    again = repo.append_import_related(original.id, replace(related, text=' SAME ', phonetic='different'))
    assert again == added and repo.connection.total_changes == changes


@pytest.mark.parametrize('area', ['name', 'arrow', 'phonetic', 'pos', 'translation', 'gap'])
def test_missing_related_entire_group_adds_once_and_switches_to_field_actions(repo, qtbot, tmp_path, area):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    right = dialog.cards[0][1]
    before_file = path.read_bytes()
    pos = None
    if area == 'gap':
        widget = right.description
        name = widget.group_cells[1][0].geometry()
        badge = widget.sense_labels[1, 0][0].geometry()
        pos = QPoint((name.right() + badge.left()) // 2, badge.center().y())
    else:
        widget = {'name': right.description.word_labels[2],
                  'arrow': right.description.findChildren(RelatedArrow)[1],
                  'phonetic': right.description.phonetic_labels[1],
                  'pos': right.description.sense_labels[1, 0][0],
                  'translation': right.description.sense_labels[1, 1][1]}[area]
    assert right.new_related == {1}
    if area == 'phonetic':
        point = widget.rect().center()
        QApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, point, widget.mapToGlobal(point)))
        assert '同步到本地关联词' in QToolTip.text()
    if pos is None:
        qtbot.mouseClick(widget, Qt.MouseButton.RightButton)
    else:
        qtbot.mouseClick(widget, Qt.MouseButton.RightButton, pos=pos)
    expected = replace(original, related=original.related + [deepcopy(right.entry.related[1])])
    assert repo.get(original.id) == expected
    assert all(not card.new_related for _, card in dialog.cards)
    assert len(dialog.conflicts) == 2 and path.read_bytes() == before_file
    assert '同步到本地关联词' not in right.description.word_labels[2].toolTip()
    assert '同步到本地音标' in right.description.phonetic_labels[1].toolTip()
    assert '同步到本地译文' in right.description.sense_labels[1, 0][1].toolTip()
    if area == 'phonetic':
        qtbot.waitUntil(lambda: not QToolTip.isVisible())
    qtbot.mouseClick(right.description.word_labels[2], Qt.MouseButton.RightButton)
    qtbot.mouseClick(right.description.sense_labels[1, 0][0], Qt.MouseButton.RightButton)
    assert repo.get(original.id) == expected and len(dialog.conflicts) == 2
    dialog.reject()


@pytest.mark.parametrize('area', ['star', 'learned', 'phonetic', 'pos', 'translation', 'existing_name', 'missing_sound'])
def test_special_positions_left_click_only_adopts_card(repo, qtbot, tmp_path, monkeypatch, area):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    right = dialog.cards[0][1]
    before_file = path.read_bytes()

    def unexpected(*args):
        pytest.fail('left adoption must not also execute a partial edit')

    for name in ('set_flag', 'set_phonetic', 'sync_import_translation', 'append_import_related'):
        monkeypatch.setattr(Repository, name, unexpected)
    monkeypatch.setattr(dialog.source, 'set_flag', unexpected)
    widget = {'star': right.star, 'learned': right.learned,
              'phonetic': right.description.phonetic_labels[-1],
              'pos': right.description.sense_labels[-1, 0][0],
              'translation': right.description.sense_labels[-1, 0][1],
              'existing_name': right.description.word_labels[1],
              'missing_sound': right.description.phonetic_labels[1]}[area]
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)
    adopted = repo.list_entries()[0]
    assert adopted.id == original.id and adopted.senses == right.entry.senses
    assert len(dialog.conflicts) == 1 and dialog.replaced_names == {'main'}
    assert path.read_bytes() == before_file
    dialog.reject()


def test_space_enter_disabled_for_entire_dialog_but_not_other_message_boxes(repo, qtbot, tmp_path):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    before_file = path.read_bytes()
    right = dialog.cards[0][1]
    for widget in (dialog, right, right.star, right.learned, right.word,
                   dialog.all_left_button, dialog.all_right_button, dialog.auto_merge_button):
        widget.setFocus()
        for key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            qtbot.keyClick(widget, key)
    assert repo.get(original.id) == original and len(dialog.conflicts) == 2
    assert path.read_bytes() == before_file and dialog.isVisible()
    message = QMessageBox(QMessageBox.Icon.Information, '提示', '内容', QMessageBox.StandardButton.Ok, dialog)
    qtbot.addWidget(message)
    message.setDefaultButton(QMessageBox.StandardButton.Ok)
    message.show()
    qtbot.keyClick(message, Qt.Key.Key_Return)
    assert not message.isVisible() and dialog.isVisible()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()


def test_right_click_on_local_fields_or_unassigned_card_areas_does_nothing(repo, qtbot, tmp_path):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    left, right = dialog.cards[0]
    before_file = path.read_bytes()
    for widget in (left.description.phonetic_labels[-1], *left.description.sense_labels[-1, 0],
                   right.word, right.description.word_labels[1]):
        qtbot.mouseClick(widget, Qt.MouseButton.RightButton)
    qtbot.mouseClick(right, Qt.MouseButton.RightButton, pos=QPoint(right.width() - 3, right.height() - 3))
    assert repo.get(original.id) == original and len(dialog.conflicts) == 2
    assert path.read_bytes() == before_file
    dialog.reject()


def test_tooltips_have_correct_priority_full_text_icons_and_survive_resize(repo, qtbot, tmp_path):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    right = dialog.cards[0][1]
    incoming = deepcopy(right.entry)
    incoming.phonetic = 'very long <sound> & ' * 30
    incoming.senses[0].translation = '<全文> &\n下一行' * 50
    incoming.related[1].text = 'very long missing related ' * 20
    right.set_entry(incoming)
    dialog.resize(720, dialog.height())
    qtbot.wait(20)
    for label, text in [(right.description.phonetic_labels[-1], incoming.phonetic),
                        (right.description.sense_labels[-1, 0][1], incoming.senses[0].translation)]:
        assert label.display_text.endswith('...')
        assert text.strip() in plain(label.toolTip())
        assert '鼠标右键' in label.toolTip()
    for widget, (owner, _, _) in right.description.targets.items():
        if owner == 1:
            assert '同步到本地关联词' in widget.toolTip()
            assert '同步到本地音标' not in widget.toolTip() and '同步到本地译文' not in widget.toolTip()
            assert incoming.related[1].text not in plain(widget.toolTip())
    assert not right.description.word_labels[1].toolTip()  # Existing a: no whole-related action.
    for control in (right.star, right.learned):
        assert control.toolTip().index('<img') < control.toolTip().index('设为重点' if control is right.star else '待记')
    assert '鼠标左键' in dialog.summary.text() and '鼠标右键' in dialog.summary.text()
    assert '相应位置修改或同步单词信息' in plain(dialog.summary.text())
    dialog.reject()


@pytest.mark.parametrize('action', ['translation', 'related'])
def test_partial_save_failure_rolls_back_and_same_right_target_can_retry(repo, qtbot, tmp_path, monkeypatch, action):
    dialog, original, path = comparison(repo, qtbot, tmp_path)
    right = dialog.cards[0][1]
    before_file = path.read_bytes()
    if action == 'translation':
        repo.connection.execute('''CREATE TRIGGER fail_sync BEFORE UPDATE OF translation ON senses
            BEGIN SELECT RAISE(ABORT,'sync locked'); END''')
        widget = right.description.sense_labels[-1, 0][1]
    else:
        repo.connection.execute('''CREATE TRIGGER fail_sync BEFORE INSERT ON senses
            WHEN NEW.translation='丁动词' BEGIN SELECT RAISE(ABORT,'sync locked'); END''')
        widget = right.description.sense_labels[1, 1][1]
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[-1]))
    qtbot.mouseClick(widget, Qt.MouseButton.RightButton)
    assert errors and 'sync locked' in errors[0]
    assert repo.get(original.id) == original and dialog.cards[0][0].entry == original
    assert len(dialog.conflicts) == 2 and right.new_related == {1} and not dialog.busy_overlay.active
    repo.connection.execute('DROP TRIGGER fail_sync')
    qtbot.mouseClick(widget, Qt.MouseButton.RightButton)
    assert repo.get(original.id) != original and len(dialog.conflicts) == 2
    assert path.read_bytes() == before_file
    dialog.reject()

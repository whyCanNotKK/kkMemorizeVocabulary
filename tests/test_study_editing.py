from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository


AM = StudyContext(date(2026, 11, 2), False)


@pytest.fixture
def study(window, repo, monkeypatch):
    entry = repo.save(WordEntry('record', [Sense('n.', '记录'), Sense('v.', '记载')],
                               [RelatedWord('recorder', [Sense('n.', '录音机'), Sense('v.', '录音')])]))
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: AM))
    window.set_filter('study17')
    page = window.study_page
    page.reveal(entry.id, True)
    yield page, entry
    page.table.delegate.cancel()


def start_edit(page, qtbot, row, column):
    index = page.model.index(row, column)
    qtbot.mouseDClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=page.table.visualRect(index).center())
    assert page.table.delegate.editor is not None
    return page.table.delegate.editor


@pytest.mark.parametrize('row,column,text', [
    (0, 1, 'records'), (0, 2, 'adj.'), (0, 3, '修改\n主词释义'), (1, 3, '第二条释义'),
    (2, 1, 'recorders'), (2, 2, 'adv.'), (2, 3, '关联词新释义'), (3, 3, '关联词第二条'),
])
def test_study_double_click_saves_master_aggregate_and_preserves_progress(
        study, window, repo, qtbot, monkeypatch, row, column, text):
    page, entry = study
    before = repo.study_backup()
    original_order = entry.sort_order
    page.table.select_entry(entry.id)
    editor = start_edit(page, qtbot, row, column)
    if column == 1:
        editor.setText(text)
    elif column == 2:
        qtbot.keyClick(editor.view(), Qt.Key.Key_3 if text == 'adj.' else Qt.Key.Key_4)
    else:
        editor.setPlainText(text)
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(AM.day, True)))
    monkeypatch.setattr(repo, 'prepare_study', lambda *args: pytest.fail('editing must not draw words'))
    window.filter_buttons['study17'].setFocus()  # ordinary focus-out submission
    qtbot.waitUntil(lambda: page.table.delegate.editor is None)
    qtbot.wait(20)
    current = repo.get(entry.id)
    target = current if row < 2 else current.related[0]
    assert (target.text if column == 1 else
            target.senses[row % 2].pos if column == 2 else target.senses[row % 2].translation) == text
    after = repo.study_backup()
    assert list(after.records.values()) == list(before.records.values())
    assert after.group_counter == before.group_counter
    assert after.study_last_add_new_vocabulary == before.study_last_add_new_vocabulary
    assert current.id == entry.id and current.sort_order == original_order
    assert current.important == entry.important and current.learned == entry.learned
    assert page.context == AM and entry.id in page.revealed
    assert page.model.rows[row].word == target
    assert page.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entry.id
    window.set_filter('all')
    window.reveal(entry.id, True)
    assert window.model.rows[row].word == target
    reopened = Repository(repo.path)
    try:
        assert reopened.get(entry.id) == current
    finally:
        reopened.close()


def test_save_shortcut_submits_study_cell_and_preserves_hidden_draft(study, window, repo, qtbot):
    page, entry = study
    window.blocks[0].word.setText('unfinished draft')
    editor = start_edit(page, qtbot, 0, 3)
    editor.setPlainText('saved by shortcut')
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: page.table.delegate.editor is None)
    assert repo.get(entry.id).senses[0].translation == 'saved by shortcut'
    assert window.blocks[0].word.text() == 'unfinished draft'
    assert not window.editor_panel.isVisible()
    assert all(entry.text != 'unfinished draft' for entry in repo.list_entries())


def test_escape_cancels_study_edit(study, repo, qtbot):
    page, entry = study
    before = repo.get(entry.id)
    editor = start_edit(page, qtbot, 0, 1)
    editor.setText('discard this')
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    assert page.table.delegate.editor is None
    assert repo.get(entry.id) == before


@pytest.mark.parametrize('failure', ['empty', 'duplicate', 'database'])
def test_invalid_edit_blocks_refresh_switch_hide_settings_export_and_close(
        study, window, repo, qtbot, monkeypatch, failure):
    page, entry = study
    repo.save(WordEntry('existing', [Sense('n.', '已有')]))
    before = repo.study_backup()
    editor = start_edit(page, qtbot, 0, 1)
    editor.setText('' if failure == 'empty' else 'existing' if failure == 'duplicate' else 'changed')
    if failure == 'database':
        def fail(*args):
            raise sqlite3.OperationalError('disk full')
        monkeypatch.setattr(repo, 'save', fail)
    assert not page.table.delegate.submit()
    if failure == 'empty':
        assert window.toast.label.text() == "单词拼写不可为空"
        anchor = editor.mapTo(window.centralWidget(), editor.rect().topLeft())
        destination = window.toast.motion_in.endValue()
        assert destination.y() + window.toast.height() < anchor.y()
    assert not page.reload()
    window.set_filter('all')
    assert window.filter_name == 'study17'
    window.set_global_hidden(True)
    assert entry.id in page.revealed
    window.open_settings()  # invalid edit must prevent opening a modal dialog
    window.export_study_progress()  # invalid edit must prevent opening a file picker
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert page.table.delegate.editor is editor
    assert repo.study_backup() == before
    page.table.delegate.cancel()


def test_switching_tabs_commits_valid_study_editor(study, window, repo, qtbot):
    page, entry = study
    editor = start_edit(page, qtbot, 0, 1)
    editor.setText('renamed')
    window.set_filter('pending')
    assert window.filter_name == 'pending'
    assert page.table.delegate.editor is None
    assert repo.get(entry.id).text == 'renamed'


def test_study_group_actions_and_blank_cells_remain_uneditable(study):
    page, _ = study
    for row, column in [(0, 0), (0, page.model.action_column), (1, 1), (3, 1)]:
        index = page.model.index(row, column)
        assert not page.model.flags(index) & Qt.ItemFlag.ItemIsEditable
        assert not page.model.setData(index, 'invalid')


def test_hidden_name_can_be_edited_without_revealing_answers(study, window, repo, qtbot):
    page, entry = study
    page.set_global_hidden(True)
    assert not page.model.flags(page.model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable
    assert not page.model.flags(page.model.index(0, 3)) & Qt.ItemFlag.ItemIsEditable
    editor = start_edit(page, qtbot, 0, 1)
    editor.setText('corrected')
    assert window.save_current()
    qtbot.wait(20)
    assert repo.get(entry.id).text == 'corrected'
    assert len(page.model.rows) == 1 and page.model.rows[0].hidden

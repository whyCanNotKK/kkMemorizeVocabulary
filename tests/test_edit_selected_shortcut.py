from dataclasses import replace

import pytest
from PySide6.QtCore import QModelIndex, QTimer, Qt
from PySide6.QtWidgets import QApplication

from vocabulary.domain import DEFAULT_SHORTCUTS, RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.shortcuts import SettingsDialog
from vocabulary.storage import Repository
from vocabulary.unsaved_input_dialog import UnsavedInputDialog


def select_word(window, entry_id, owner=-1, sense=0):
    row = next(i for i, row in enumerate(window.model.rows)
               if row.entry.id == entry_id and row.owner == owner and row.sense == sense)
    window.table.setCurrentIndex(window.model.index(row, 1))


def edit_key(qtbot, target):
    qtbot.keyClick(target, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)


@pytest.mark.parametrize('page', ['pending', 'all', 'learned'])
@pytest.mark.parametrize('owner,sense', [(-1, 0), (-1, 1), (0, 0)])
def test_edit_selected_loads_whole_group_on_ordinary_pages(window, repo, qtbot, page, owner, sense):
    entry = repo.save(WordEntry('main', [Sense('n.', '名词'), Sense('v.', '动词')],
                                [RelatedWord('child', [Sense('n.', '关联词')], '/child/')],
                                learned=page == 'learned', phonetic='/main/'))
    window.set_filter(page)
    window.set_global_hidden(False)
    select_word(window, entry.id, owner, sense)
    window.set_form_collapsed(True)
    window.search_box.setFocus()
    edit_key(qtbot, window.search_box)
    assert window.edit_id == entry.id
    assert window.form_value() == replace(entry, learned=False, sort_order=None)
    assert not window.form_collapsed and window.blocks[0].word.hasFocus()


def test_hidden_word_can_be_loaded_and_same_word_preserves_existing_draft(window, repo, qtbot, monkeypatch):
    entry = repo.save(WordEntry('hidden', [Sense('n.', '隐藏')]))
    window.refresh()
    select_word(window, entry.id)
    assert window.model.rows[0].hidden
    window.table.setFocus()
    edit_key(qtbot, window.table)
    assert window.edit_id == entry.id and window.model.rows[0].hidden
    window.blocks[0].word.setText('draft')
    draft = window.form_value()
    window.set_form_collapsed(True)
    with monkeypatch.context() as patch:
        patch.setattr(window, 'resolve_dirty', lambda: pytest.fail('Same entry must keep its draft'))
        window.table.setFocus()
        edit_key(qtbot, window.table)
    assert window.form_value() == draft and not window.form_collapsed


@pytest.mark.parametrize('choice', ['save', 'discard', 'escape', 'close', 'invalid_save'])
def test_edit_selected_uses_existing_unsaved_input_dialog(window, repo, qtbot, choice):
    first = repo.save(WordEntry('first', [Sense('n.', '原词')]))
    second = repo.save(WordEntry('second', [Sense('v.', '目标词')]))
    window.refresh()
    window.reset_form(first)
    window.blocks[0].word.setText('' if choice == 'invalid_save' else 'draft')
    draft = window.form_value()
    select_word(window, second.id)
    window.table.setFocus()
    seen = []

    def respond():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, UnsavedInputDialog)
            seen.append(dialog.windowTitle())
            if choice in ('save', 'invalid_save'):
                dialog.save_button.click()
            elif choice == 'discard':
                dialog.discard_button.click()
            elif choice == 'escape':
                qtbot.keyClick(dialog, Qt.Key.Key_Escape)
            else:
                dialog.close()
        finally:
            if dialog is not None and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(10, respond)
    edit_key(qtbot, window.table)
    assert seen == ['是否保留当前输入？']
    assert window.edit_id == (second.id if choice in ('save', 'discard') else first.id)
    assert repo.get(first.id).text == ('draft' if choice == 'save' else 'first')
    if choice not in ('save', 'discard'):
        assert window.form_value() == draft


@pytest.mark.parametrize('selection', ['none', 'cleared', 'study17'])
def test_edit_selected_does_nothing_without_selection_or_on_study_page(window, repo, qtbot, monkeypatch, selection):
    entry = repo.save(WordEntry('selected', [Sense('n.', '词')]))
    window.refresh()
    select_word(window, entry.id)
    if selection == 'none':
        window.table.setCurrentIndex(QModelIndex())
    elif selection == 'cleared':
        window.table.clearSelection()
    else:
        window.set_filter('study17')
        if window.study_page.table.model().rowCount():
            window.study_page.table.setCurrentIndex(window.study_page.table.model().index(0, 1))
    window.blocks[0].word.setText('keep draft')
    draft = window.form_value()
    monkeypatch.setattr(window, 'list_action', lambda *args: pytest.fail('No editable selected word'))
    table = window.current_table()
    table.setFocus()
    edit_key(qtbot, table)
    assert window.edit_id is None and window.form_value() == draft
    if selection == 'study17':
        assert not window.editor_panel.isVisible()


@pytest.mark.parametrize('valid', [True, False])
def test_edit_selected_submits_inline_edit_and_blocks_on_failure(window, repo, qtbot, valid):
    entry = repo.save(WordEntry('word', [Sense('n.', '词')], [RelatedWord('child', [Sense('n.', 'child')])]))
    window.refresh()
    select_word(window, entry.id)
    index = window.table.currentIndex()
    window.table.edit(index)
    editor = window.table.delegate.editor
    editor.setText('updated' if valid else '')
    edit_key(qtbot, editor)
    if valid:
        assert window.table.delegate.editor is None
        assert window.edit_id == entry.id and window.blocks[0].word.text() == 'updated'
        assert repo.get(entry.id).text == 'updated'
    else:
        assert window.table.delegate.editor is editor and window.edit_id is None
        assert repo.get(entry.id).text == 'word'


def test_edit_shortcut_settings_remap_restore_and_persist(window, repo, qtbot, monkeypatch):
    assert window.shortcuts.values['toggle_visibility'] == 'Ctrl+S'
    assert window.shortcuts.values['edit_selected'] == 'Ctrl+E'
    dialog = SettingsDialog(window.shortcuts, window)
    qtbot.addWidget(dialog)
    assert dialog.editors['edit_selected'].text() == 'Ctrl+E'
    window.shortcuts.update(dict(window.shortcuts.values, edit_selected='Alt+D'))
    assert dialog.editors['edit_selected'].text() == 'Alt+D'
    with pytest.raises(ValidationError):
        window.shortcuts.update(dict(window.shortcuts.values, edit_selected='Ctrl+S'))
    entry = repo.save(WordEntry('edit', [Sense('n.', '词')]))
    window.refresh()
    select_word(window, entry.id)
    calls = []
    monkeypatch.setattr(window, 'list_action', lambda *args: calls.append(args))
    window.table.setFocus()
    edit_key(qtbot, window.table)
    assert calls == []
    qtbot.keyClick(window.table, Qt.Key.Key_D, Qt.KeyboardModifier.AltModifier)
    assert calls == [(entry.id, 'edit')]
    reopened = Repository(repo.path)
    try:
        assert reopened.shortcuts()['edit_selected'] == 'Alt+D'
    finally:
        reopened.close()
    dialog.restore()
    assert window.shortcuts.values == DEFAULT_SHORTCUTS
    assert dialog.editors['edit_selected'].text() == 'Ctrl+E'


@pytest.mark.parametrize('visibility,occupied,expected', [
    ('Ctrl+E', None, ('Ctrl+S', 'Ctrl+E')),
    ('Alt+E', None, ('Alt+E', 'Ctrl+E')),
    ('Ctrl+E', 'Ctrl+S', ('Ctrl+E', 'Ctrl+Shift+E')),
    ('Alt+E', 'Ctrl+E', ('Alt+E', 'Ctrl+Shift+E')),
])
def test_old_shortcuts_upgrade_without_overwriting_custom_bindings(repo, visibility, occupied, expected):
    with repo.connection:
        repo.connection.execute("DELETE FROM settings WHERE key='edit_selected'")
    values = {'toggle_visibility': visibility}
    if occupied:
        values['save'] = occupied
    repo.write_shortcuts(values)
    for _ in range(2):
        reopened = Repository(repo.path)
        try:
            actual = reopened.shortcuts()
            assert (actual['toggle_visibility'], actual['edit_selected']) == expected
            assert len(actual.values()) == len(set(actual.values()))
            if occupied:
                assert actual['save'] == occupied
        finally:
            reopened.close()

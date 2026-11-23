import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry


@pytest.fixture
def modifier():
    return Qt.KeyboardModifier.AltModifier


def press_tab(window, qtbot, number, modifier):
    qtbot.keyClick(QApplication.focusWidget() or window, getattr(Qt.Key, f"Key_{number}"),
                   modifier)


@pytest.mark.parametrize("focus", ["form", "pos", "translation", "search", "list"])
def test_tabs_switch_from_any_main_window_input(window, qtbot, focus, modifier):
    {"form": window.blocks[0].word, "search": window.search_box,
     "pos": window.blocks[0].rows[0].pos, "translation": window.blocks[0].rows[0].translation,
     "list": window.table}[focus].setFocus()
    for number, page in [(2, "all"), (3, "learned"), (4, "study17"), (1, "pending")]:
        press_tab(window, qtbot, number, modifier)
        assert window.filter_name == page
        assert QApplication.focusWidget() is window.current_table()


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("number", [2, 3, 4])
def test_tab_switch_preserves_independent_form(window, repo, qtbot, editing, number, modifier, monkeypatch):
    saved = repo.save(WordEntry("original", [Sense("n.", "原词")]))
    if editing:
        window.reset_form(saved)
    block = window.blocks[0]
    block.word.setText("changed")
    block.rows[0].pos.set_pos("n.")
    block.rows[0].translation.setPlainText("修改")
    block.word.setFocus()
    draft = window.form_value()

    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("tab switch must not process draft"))
    press_tab(window, qtbot, number, modifier)
    assert window.filter_name == {2: "all", 3: "learned", 4: "study17"}[number]
    assert window.form_value() == draft and window.is_dirty()
    assert repo.get(saved.id).text == "original"
    window.set_filter("pending")
    assert window.form_value() == draft


def test_incomplete_draft_does_not_stop_tab_switch(window, repo, qtbot, modifier):
    window.blocks[0].word.setText("incomplete")
    press_tab(window, qtbot, 4, modifier)
    assert window.filter_name == "study17" and window.is_dirty()
    assert window.blocks[0].word.text() == "incomplete" and repo.list_entries() == []


def test_tab_switch_submits_inline_edit_and_blocks_invalid_input(window, repo, qtbot, modifier):
    saved = repo.save(WordEntry("original", [Sense("n.", "原词")],
                                [RelatedWord("child", [Sense("n.", "child")])]))
    window.reveal(saved.id, True)
    window.table.edit(window.model.index(0, 1))
    editor = window.table.delegate.editor
    editor.setText("")
    press_tab(window, qtbot, 2, modifier)
    assert window.filter_name == "pending" and window.table.delegate.editor is editor
    assert QApplication.focusWidget() is editor
    editor.setText("new name")
    press_tab(window, qtbot, 2, modifier)
    assert window.filter_name == "all" and window.table.delegate.editor is None
    assert repo.get(saved.id).text == "new name"


@pytest.mark.parametrize('page', ['pending', 'all', 'learned', 'study17'])
def test_mouse_tab_switch_preserves_draft_and_focuses_list(window, qtbot, monkeypatch, page):
    window.blocks[0].word.setText("draft")
    monkeypatch.setattr(window, "resolve_dirty", lambda: not window.is_dirty() or pytest.fail("tab switch must not process draft"))
    qtbot.mouseClick(window.filter_buttons[page], Qt.MouseButton.LeftButton)
    assert window.filter_name == page and window.blocks[0].word.text() == "draft"
    assert QApplication.focusWidget() is window.current_table()
    assert not window.filter_buttons[page].hasFocus()
    assert not window.current_table()._mouse_moved


def test_alt_tab_then_space_does_not_activate_previous_tab_button(window, qtbot, modifier):
    qtbot.mouseClick(window.filter_buttons["study17"], Qt.MouseButton.LeftButton)
    press_tab(window, qtbot, 1, modifier)
    assert window.filter_name == "pending"
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
    assert window.filter_name == "pending"
    assert QApplication.focusWidget() is window.table


@pytest.mark.parametrize('trigger', ['keyboard', 'mouse'])
def test_tabs_restore_each_pages_own_word_and_cell(window, repo, qtbot, modifier, monkeypatch, trigger):
    from datetime import date
    from vocabulary.learning import StudyContext

    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 1), False)))
    for i in range(36):
        repo.save(WordEntry(f'word-{i}', [Sense('n.', '释义'), Sense('v.', '补充')],
                            [RelatedWord('child', [Sense('n.', '关联')])], learned=i % 3 == 0))
    expected = {}

    def switch(number, page):
        if trigger == 'mouse':
            qtbot.mouseClick(window.filter_buttons[page], Qt.MouseButton.LeftButton)
        else:
            press_tab(window, qtbot, number, modifier)

    for number, page in [(1, 'pending'), (2, 'all'), (3, 'learned'), (4, 'study17')]:
        switch(number, page)
        assert QApplication.focusWidget() is window.current_table()
        table = window.current_table()
        assert table.selectionModel().isSelected(table.currentIndex())
        window.set_global_hidden(False)
        table.setCurrentIndex(table.model().index(number * 4 + 2, 3))
        expected[page] = table.view_state()[:2]
    for number, page in [(3, 'learned'), (2, 'all'), (1, 'pending'), (4, 'study17')]:
        switch(number, page)
        table = window.current_table()
        assert table.view_state()[:2] == expected[page]
        assert QApplication.focusWidget() is table
        assert table.visualRect(table.currentIndex()).intersects(table.viewport().rect())
        assert not any(button.hasFocus() for button in window.filter_buttons.values())
        if page != 'study17':
            qtbot.keyClick(table, Qt.Key.Key_Space)
            assert window.filter_name == page


def test_alt_current_page_focuses_remembered_word_with_minimal_scroll(window, repo, qtbot, modifier):
    entries = [repo.save(WordEntry(f'word-{i}', [Sense('n.', '释义')])) for i in range(50)]
    window.refresh()
    window.table.select_entry(entries[-1].id)
    window.table.scrollToTop()
    window.search_box.setFocus()
    press_tab(window, qtbot, 1, modifier)
    assert QApplication.focusWidget() is window.table
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == entries[-1].id
    assert window.table.visualRect(window.table.currentIndex()).intersects(window.table.viewport().rect())


def test_failed_alt_reload_keeps_original_focus(window, repo, qtbot, modifier, monkeypatch):
    def fail(*args):
        raise OSError('载入失败')

    window.set_filter('study17')
    button = window.filter_buttons['study17']
    button.setFocus()
    monkeypatch.setattr(repo, 'prepare_study', fail)
    press_tab(window, qtbot, 4, modifier)
    assert window.filter_name == 'study17'
    assert QApplication.focusWidget() is button


def test_alt_tab_falls_back_when_remembered_word_was_deleted(window, repo, qtbot, modifier):
    removed = repo.save(WordEntry('removed', [Sense('n.', '删除')]))
    remaining = repo.save(WordEntry('remaining', [Sense('n.', '保留')]))
    window.refresh()
    window.table.select_entry(removed.id)
    press_tab(window, qtbot, 2, modifier)
    repo.delete(removed.id)
    press_tab(window, qtbot, 1, modifier)
    assert window.table.hasFocus()
    assert window.table.currentIndex().data(Qt.ItemDataRole.UserRole).entry.id == remaining.id
    assert window.table.selectionModel().isSelected(window.table.currentIndex())


def test_tab_shortcuts_suspend_and_do_not_repeat(window, qtbot, modifier):
    window.shortcuts.set_suspended(True)
    press_tab(window, qtbot, 2, modifier)
    assert window.filter_name == "pending"
    window.shortcuts.set_suspended(False)
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, modifier, "", True)
    QApplication.sendEvent(window.blocks[0].word, repeat)
    assert window.filter_name == "pending"
    for number in range(1, 5):
        with pytest.raises(ValidationError):
            prefix = "Alt" if modifier == Qt.KeyboardModifier.AltModifier else "Ctrl"
            window.shortcuts.update(dict(window.shortcuts.values, save=f"{prefix}+{number}"))


def test_only_alt_digits_dispatch_tab_switch_once(window, qtbot, monkeypatch):
    calls = []
    original = window.set_filter

    def switch(key, **kwargs):
        calls.append(key)
        return original(key, **kwargs)

    monkeypatch.setattr(window, "set_filter", switch)
    for modifier in (Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.ControlModifier):
        for number in (2, 3, 4, 1):
            press_tab(window, qtbot, number, modifier)
    assert calls == ["all", "learned", "study17", "pending"]


def test_form_title_tracks_editing_and_reset(window, repo):
    saved = repo.save(WordEntry("original", [Sense("n.", "原词")]))
    assert window.form_title.text() == "添加单词"
    window.reset_form(saved)
    assert window.form_title.text() == "编辑单词"
    window.set_form_collapsed(True)
    assert window.collapse_button.toolTip() == "展开编辑单词区域 · Alt+↑"
    QTimer.singleShot(10, lambda: QApplication.activeModalWidget().accept())
    window.cancel_form()
    assert window.form_title.text() == "添加单词"
    window.reset_form(saved)
    assert window._save_form()
    assert window.form_title.text() == "添加单词"



def press_arrow(window, qtbot, key):
    qtbot.keyClick(QApplication.focusWidget() or window, key, Qt.KeyboardModifier.AltModifier)


def test_alt_horizontal_arrows_switch_adjacent_tabs_without_wrap_and_update_tips(window, qtbot):
    window.search_box.setFocus()
    press_arrow(window, qtbot, Qt.Key.Key_Left)
    assert window.filter_name == 'pending' and window.search_box.hasFocus()
    for page in ('all', 'learned', 'study17'):
        press_arrow(window, qtbot, Qt.Key.Key_Right)
        assert window.filter_name == page and window.current_table().hasFocus()
        for number, (key, button) in enumerate(window.filter_buttons.items(), 1):
            assert button.toolTip() == f'Alt+{number}' + (' · Alt+←/→' if key != page else '')
    press_arrow(window, qtbot, Qt.Key.Key_Right)
    assert window.filter_name == 'study17'
    for page in ('learned', 'all', 'pending'):
        press_arrow(window, qtbot, Qt.Key.Key_Left)
        assert window.filter_name == page and window.current_table().hasFocus()


@pytest.mark.parametrize('page', ['pending', 'all', 'learned', 'study17'])
def test_alt_vertical_arrows_set_section_state_and_keep_list_focus(window, qtbot, page):
    window.activate_filter(page)
    table = window.current_table()
    state = table.view_state()
    panel = window.history_panel if page == 'study17' else window
    button = window.history_panel.collapse_button if page == 'study17' else window.collapse_button
    def collapsed():
        return panel.collapsed if page == 'study17' else panel.form_collapsed
    for key, expected in ((Qt.Key.Key_Down, True), (Qt.Key.Key_Down, True),
                          (Qt.Key.Key_Up, False), (Qt.Key.Key_Up, False)):
        press_arrow(window, qtbot, key)
        qtbot.wait(10)
        assert collapsed() is expected
        assert table.hasFocus() and table.view_state()[:2] == state[:2]
        assert button.toolTip().endswith(' · Alt+↑' if expected else ' · Alt+↓')


def test_alt_section_does_not_restore_old_form_focus_and_preserves_search_focus(window, qtbot):
    edit = window.blocks[0].word
    edit.setText('draft')
    edit.setFocus()
    press_arrow(window, qtbot, Qt.Key.Key_Down)
    qtbot.wait(20)
    assert window.form_collapsed and window.table.hasFocus()
    assert not window.collapse_button.hasFocus()
    window.search_box.setFocus()
    press_arrow(window, qtbot, Qt.Key.Key_Up)
    qtbot.wait(20)
    assert not window.form_collapsed and window.search_box.hasFocus()
    assert edit.text() == 'draft'
    press_arrow(window, qtbot, Qt.Key.Key_Down)
    assert window.search_box.hasFocus()


def test_alt_arrows_respect_invalid_inline_edit_suspension_and_repeat(window, repo, qtbot):
    entry = repo.save(WordEntry('word', [Sense('n.', '词')], [RelatedWord('child', [Sense('n.', '子词')])]))
    window.reveal(entry.id, True)
    window.table.edit(window.model.index(0, 1))
    editor = window.table.delegate.editor
    editor.setText('')
    press_arrow(window, qtbot, Qt.Key.Key_Right)
    assert window.filter_name == 'pending' and editor.hasFocus()
    editor.setText('saved')
    press_arrow(window, qtbot, Qt.Key.Key_Right)
    assert window.filter_name == 'all' and window.table.hasFocus()
    assert repo.get(entry.id).text == 'saved'
    window.shortcuts.set_suspended(True)
    press_arrow(window, qtbot, Qt.Key.Key_Right)
    assert window.filter_name == 'all'
    window.shortcuts.set_suspended(False)
    QApplication.sendEvent(window.table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                           Qt.KeyboardModifier.AltModifier, '', True))
    assert window.filter_name == 'all'


@pytest.mark.parametrize('direction', ['Left', 'Right', 'Up', 'Down'])
def test_alt_arrows_are_reserved(window, direction):
    with pytest.raises(ValidationError):
        window.shortcuts.update(dict(window.shortcuts.values, save=f'Alt+{direction}'))



def test_alt_section_collapsing_focused_history_moves_to_word_list(window, qtbot):
    window.activate_filter('study17')
    window.history_panel.set_collapsed(False)
    window.history_panel.table.setFocus()
    press_arrow(window, qtbot, Qt.Key.Key_Down)
    qtbot.wait(20)
    assert window.history_panel.collapsed and window.study_page.table.hasFocus()
    press_arrow(window, qtbot, Qt.Key.Key_Up)
    qtbot.wait(20)
    assert not window.history_panel.collapsed and window.study_page.table.hasFocus()


def test_alt_arrow_legacy_bindings_are_repaired_without_duplicate_defaults(repo, qtbot):
    from vocabulary.domain import DEFAULT_SHORTCUTS
    from vocabulary.window import MainWindow
    repo.write_shortcuts(dict(DEFAULT_SHORTCUTS, save='Alt+Left', add_related=DEFAULT_SHORTCUTS['save']))
    reopened = MainWindow(repo)
    qtbot.addWidget(reopened)
    assert reopened.shortcuts.values['save'] == DEFAULT_SHORTCUTS['save']
    assert len(set(reopened.shortcuts.values.values())) == len(DEFAULT_SHORTCUTS)
    assert reopened.shortcuts.active_bindings['Alt+Left'] == 'tab_previous'
    reopened._closing = True

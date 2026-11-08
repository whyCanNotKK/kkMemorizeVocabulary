"""Navigation enters the active word list from review controls, never text inputs."""
from datetime import date

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.learning import StudyContext


@pytest.fixture(params=["pending", "all", "learned", "study17"])
def review_area(window, repo, qtbot, monkeypatch, request):
    context = StudyContext(date(2026, 11, 1), False)
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: context))
    for i in range(30):
        repo.save(WordEntry(f"word-{i}", [Sense("n.", "释义"), Sense("v.", "补充")],
                            [RelatedWord("child", [Sense("n.", "关联")])],
                            learned=request.param == "learned"))
    window.set_filter(request.param)
    window.set_global_hidden(False)
    table = window.study_page.table if request.param == "study17" else window.table
    qtbot.wait(20)
    return table


def primary_indexes(table):
    return [table.model().index(i, 1) for i, row in enumerate(table.model().rows) if row.first]


def test_toolbar_navigation_uses_active_table(review_area, window, qtbot):
    table = review_area
    primary = primary_indexes(table)
    controls = [*window.filter_buttons.values(), window.settings_button, window.global_hide_button,
                window.import_button, window.export_button]
    controls += [window.study_page.refresh_button] if table.study_mode else [table.shuffle_button]
    for control in controls:
        if not control.isVisible() or not control.isEnabled():
            continue
        for key, target in ((Qt.Key.Key_Up, 0), (Qt.Key.Key_W, 0),
                            (Qt.Key.Key_Down, 2), (Qt.Key.Key_S, 2)):
            table.setCurrentIndex(primary[1])
            control.setFocus()
            assert control.hasFocus()
            with qtbot.assertNotEmitted(control.clicked):
                qtbot.keyClick(control, key)
            assert table.hasFocus()
            assert table.currentIndex() == primary[target]
            selected = table.selectionModel().selectedRows()
            assert {index.data(Qt.ItemDataRole.UserRole).entry.id for index in selected} == {
                primary[target].data(Qt.ItemDataRole.UserRole).entry.id}


def test_toolbar_navigation_without_selection_keeps_viewport(review_area, window, qtbot):
    table = review_area
    primary = primary_indexes(table)
    bar = table.verticalScrollBar()
    position = table.rowViewportPosition(primary[8].row()) + bar.value() + 2
    for key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_W, Qt.Key.Key_S):
        table.setCurrentIndex(QModelIndex())
        bar.setValue(position)
        control = window.filter_buttons[window.filter_name]
        control.setFocus()
        qtbot.keyClick(control, key)
        assert table.hasFocus()
        assert table.currentIndex() == primary[8]
        assert bar.value() == position


def test_search_and_form_inputs_keep_navigation_keys(window, repo, qtbot):
    repo.save(WordEntry("word", [Sense("n.", "释义")]))
    window.refresh()
    table = window.table
    table.setCurrentIndex(window.model.index(0, 1))
    for field in (window.search_box, window.blocks[0].word, window.blocks[0].rows[0].translation):
        field.setFocus()
        qtbot.keyClicks(field, "ws")
        assert (field.text() if hasattr(field, "text") else field.toPlainText()) == "ws"
        for key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            qtbot.keyClick(field, key)
        assert QApplication.focusWidget() is field
    window.add_related_button.setFocus()
    qtbot.keyClick(window.add_related_button, Qt.Key.Key_W)
    assert not table.hasFocus()


def test_empty_list_receives_focus_from_filter_button(window, qtbot):
    table = window.table
    for key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_W, Qt.Key.Key_S):
        control = window.filter_buttons["pending"]
        control.setFocus()
        qtbot.keyClick(control, key)
        assert table.hasFocus()
        assert not table.currentIndex().isValid()


def test_modified_keys_on_toolbar_do_not_route_to_table(review_area, window, qtbot):
    table = review_area
    table.setCurrentIndex(primary_indexes(table)[1])
    current = table.currentIndex()
    control = window.filter_buttons[window.filter_name]
    for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.AltModifier,
                     Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.MetaModifier):
        control.setFocus()
        qtbot.keyClick(control, Qt.Key.Key_W, modifier)
        assert not table.hasFocus()
        assert table.currentIndex() == current


def test_navigation_does_not_force_commit_or_focus_from_invalid_editor(window, repo, qtbot):
    entry = repo.save(WordEntry("word", [Sense("n.", "释义")],
                                [RelatedWord("child", [Sense("n.", "child")])]))
    window.refresh()
    table = window.table
    table.setCurrentIndex(window.model.index(0, 1))
    table.edit(table.currentIndex())
    editor = table.delegate.editor
    editor.setText("")
    window.settings_button.setFocus()
    qtbot.keyClick(window.settings_button, Qt.Key.Key_S)
    assert table.delegate.editor is editor
    assert editor.text() == ""
    assert repo.get(entry.id).text == "word"
    assert not table.hasFocus()
    table.delegate.cancel()


@pytest.mark.parametrize("modal", [False, True])
def test_other_window_keeps_its_keyboard_focus(window, qtbot, modal):
    dialog = QDialog(window)
    dialog.setModal(modal)
    layout = QVBoxLayout(dialog)
    control = QPushButton("对话框按钮", dialog)
    layout.addWidget(control)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    control.setFocus()
    qtbot.wait(20)
    for key in (Qt.Key.Key_W, Qt.Key.Key_S, Qt.Key.Key_Up, Qt.Key.Key_Down):
        qtbot.keyClick(control, key)
        assert dialog.isActiveWindow()
        assert not window.table.hasFocus()
    dialog.close()

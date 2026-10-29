"""Opt-in acceptance run for source and bundled builds with isolated data."""
import json
from pathlib import Path
import traceback

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from .shortcuts import SettingsDialog
from .import_dialog import ImportConflictDialog
from .transfer import read_entries, serialize_entries, write_entries


def run_smoke(app, window, directory: Path):
    report = {"ok": False, "checks": [], "platform": app.platformName()}
    try:
        window.activateWindow()
        window.raise_()
        QTest.qWait(40)
        assert window.windowTitle() == "kkMemorizeVocabulary"
        assert not any(label.text() == "单词本" for label in window.findChildren(QLabel))
        assert window.global_hide_button.isChecked()
        assert all(row.hidden for row in window.model.rows)
        assert window.settings_button.text() == "快捷键设置"
        assert window.add_related_button.text().startswith("新增关联词  ")
        assert window.remove_related_button.isHidden()
        footer = (window.add_related_button, window.remove_related_button,
                  window.star_button, window.save_button)
        centers = [control.mapTo(window, control.rect().center()).y()
                   for control in footer if control.isVisible()]
        assert max(centers) - min(centers) <= 2
        tabs = list(window.filter_buttons.values())
        assert abs(window.settings_button.mapTo(window, QPoint(0, 0)).y()
                   - tabs[0].mapTo(window, QPoint(0, 0)).y()) <= 4
        assert window.settings_button.mapTo(window, QPoint(0, 0)).x() > tabs[-1].mapTo(window, QPoint(0, 0)).x()
        report["checks"].append("compact_header_and_single_footer_row")
        block = window.blocks[0]
        row = block.rows[0]
        assert block.word.height() == row.pos.height() == row.translation.height()
        assert row.pos.width() < 120
        assert row.translation.verticalScrollBar().maximum() == 0
        assert block.add_button.text().startswith("增加释义")
        assert block.remove_button.text().startswith("删除释义")
        assert not window.toast.isVisible()
        initial_editor_height = window.editor_panel.height()
        assert 180 <= initial_editor_height < 250
        assert window.form_scroll.verticalScrollBar().maximum() == 0
        report["checks"].append("compact_inputs_and_no_status_footer")
        repo = window.repository
        existing = repo.list_entries()
        if existing:
            saved = existing[0]
            assert saved.text == "serendipity"
            assert saved.senses[0].translation == "意外发现美好事物的能力\n机缘巧合"
            assert len(saved.related) == 2
            assert window.shortcuts.values["add_related"] == "Alt+N"
            report["checks"].append("restart_restored_data_and_custom_shortcuts")
        else:
            block = window.blocks[0]
            block.word.setText("serendipity")
            block.rows[0].pos.setFocus()
            QTest.keyClick(block.rows[0].pos, Qt.Key.Key_1)
            block.rows[0].translation.setPlainText("意外发现美好事物的能力\n机缘巧合")
            QTest.keyClick(block.rows[0].translation, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
            QTest.qWait(30)
            child = window.blocks[-1]
            assert window.remove_related_button.isVisible()
            child.word.setText("serendipitous")
            child.rows[0].pos.setFocus()
            QTest.keyClick(child.rows[0].pos, Qt.Key.Key_3)
            child.rows[0].translation.setPlainText("偶然发现的；机缘巧合的")
            window.add_related()
            QTest.qWait(20)
            child = window.blocks[-1]
            child.word.setText("serenity")
            child.rows[0].pos.set_pos("n.")
            child.rows[0].translation.setPlainText("平静；安宁")
            QTest.keyClick(child.rows[0].translation, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
            QTest.qWait(30)
            saved = repo.list_entries()[0]
            assert saved.text == "serendipity" and len(saved.related) == 2
            assert not any(row.hidden for row in window.model.rows)
            bar = window.table.verticalScrollBar()
            assert bar.value() == bar.maximum()
            report["checks"].append("new_word_expanded_at_bottom")
            report["checks"].append("keyboard_entry_and_transactional_save")
            window.shortcuts.update(dict(window.shortcuts.values, add_related="Alt+N"))
            assert repo.shortcuts()["add_related"] == "Alt+N"
            report["checks"].append("shortcut_database_and_runtime_match")
        QTest.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
        assert not any(row.hidden for row in window.model.rows)
        QTest.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
        assert window.model.rowCount() == 1 and window.model.rows[0].hidden
        QTest.mouseClick(window.global_hide_button, Qt.MouseButton.LeftButton)
        assert not any(row.hidden for row in window.model.rows)
        report["checks"].append("global_show_hide_toggle")
        QTest.qWait(60)
        index = window.model.index(1, 1)
        QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.table.visualRect(index).center())
        assert len(window.table.selectionModel().selectedRows()) == 3
        assert window.table.currentIndex() == index
        report["checks"].append("related_click_selects_entire_group")
        QTest.qWait(60)
        window.grab().save(str(directory / "main-expanded.png"))
        assert window.model.rowCount() == 3
        index = window.model.index(0, 3)
        QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier, window.table.visualRect(index).center())
        assert window.model.rowCount() == 1 and window.model.rows[0].hidden
        report["checks"].append("single_mask_and_group_hide")
        QTest.qWait(30)
        window.grab().save(str(directory / "main-hidden.png"))
        window.list_action(saved.id, "edit")
        child = window.blocks[1]
        child.rows[0].translation.setFocus()
        QTest.qWait(80)
        target = child.rows[0].translation
        window.ensure_focus_visible(target)
        QTest.qWait(20)
        top = target.mapTo(window.form_scroll.viewport(), QPoint(0, 0)).y()
        report["editor_geometry"] = {"top": top, "height": target.height(),
                                     "viewport_height": window.form_scroll.viewport().height(),
                                     "focused": target.hasFocus()}
        assert top >= 0 and top + target.height() <= window.form_scroll.viewport().height()
        window.grab().save(str(directory / "form-editing.png"))
        assert "Ctrl+J" in child.add_button.text()
        assert "Ctrl+J" not in window.blocks[0].add_button.text()
        report["checks"].append("focus_scoped_hints")
        window.reset_form()
        window.shortcuts.set_suspended(True)
        dialog = SettingsDialog(window.shortcuts, window)
        dialog.show()
        QTest.qWait(40)
        dialog.grab().save(str(directory / "settings.png"))
        dialog.close()
        window.shortcuts.set_suspended(False)
        report["checks"].append("settings_window")
        window.activateWindow()
        QTest.qWait(20)
        field = window.blocks[0].word
        field.setFocus()
        QTest.qWait(20)
        assert not window._save_form()
        QTest.qWait(220)
        assert window.toast.isVisible() and field.hasFocus()
        window.grab().save(str(directory / "floating-notice.png"))
        QTest.qWait(2250)
        assert not window.toast.isVisible()
        report["checks"].append("floating_error_notice_and_auto_dismiss")
        expanded_height = window.editor_panel.height()
        QTest.mouseClick(window.collapse_button, Qt.MouseButton.LeftButton)
        QTest.qWait(40)
        assert window.form_collapsed and window.editor_panel.height() <= 60
        assert not window.editor_body.isVisible()
        window.grab().save(str(directory / "form-collapsed.png"))
        QTest.mouseClick(window.collapse_button, Qt.MouseButton.LeftButton)
        QTest.qWait(40)
        assert not window.form_collapsed and field.hasFocus()
        assert abs(window.editor_panel.height() - expanded_height) <= 2
        report["checks"].append("collapse_expand_and_restore_form")
        for _ in range(3):
            window.blocks[0].add_sense()
        QTest.qWait(40)
        assert initial_editor_height < window.editor_panel.height() <= window.centralWidget().height() // 2
        if window.editor_panel.height() < window.centralWidget().height() // 2:
            assert window.form_scroll.verticalScrollBar().maximum() == 0
        for _ in range(5):
            window.add_related()
        QTest.qWait(50)
        assert window.editor_panel.height() == window.centralWidget().height() // 2
        assert window.form_scroll.verticalScrollBar().maximum() > 0
        window.grab().save(str(directory / "form-automatic-scroll.png"))
        window.reset_form()
        QTest.qWait(40)
        assert window.editor_panel.height() == initial_editor_height
        assert window.form_scroll.verticalScrollBar().maximum() == 0
        report["checks"].append("automatic_form_height_and_scroll_limit")
        entries = repo.list_entries()
        write_entries(directory / "all-words.json", entries)
        imported = read_entries(directory / "all-words.json")
        assert serialize_entries(imported) == serialize_entries(entries)
        result = repo.import_entries(imported)
        assert result.inserted == 0 and len(result.conflicts) == len(entries)
        comparison = ImportConflictDialog(repo, result, window)
        comparison.show()
        QTest.qWait(40)
        comparison.grab().save(str(directory / "import-comparison.png"))
        QTest.mouseClick(comparison.cards[0][1].description, Qt.MouseButton.LeftButton)
        assert len(comparison.conflicts) == len(entries) - 1
        adopted = next(entry for entry in repo.list_entries()
                       if entry.text.casefold() == entries[0].text.casefold())
        assert adopted.id != entries[0].id
        from dataclasses import replace
        assert replace(adopted, id=entries[0].id, sort_order=entries[0].sort_order) == entries[0]
        comparison.close()
        report["checks"].append("json_roundtrip_and_immediate_card_import")
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (directory / "smoke-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.table.delegate.cancel()
        window._baseline = window.form_value()
        app.exit(0 if report["ok"] else 1)

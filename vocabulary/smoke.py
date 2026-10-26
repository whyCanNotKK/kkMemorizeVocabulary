"""Opt-in acceptance run for source and bundled builds with isolated data."""
import json
from pathlib import Path
import traceback

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from .shortcuts import SettingsDialog


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
        footer = (window.settings_button, window.add_related_button, window.remove_related_button,
                  window.star_button, window.save_button)
        centers = [control.mapTo(window, control.rect().center()).y() for control in footer]
        assert max(centers) - min(centers) <= 2
        report["checks"].append("compact_header_and_single_footer_row")
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
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (directory / "smoke-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.table.delegate.cancel()
        window._baseline = window.form_value()
        app.exit(0 if report["ok"] else 1)

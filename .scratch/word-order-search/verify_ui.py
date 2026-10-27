"""Isolated native/offscreen visual acceptance; never uses the default database."""
import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.storage import Repository
from vocabulary.theme import apply_theme
from vocabulary.transfer import write_entries
from vocabulary.window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_theme(app)
    repo = Repository(args.data_dir / "vocabulary.sqlite3")
    if not repo.list_entries():
        repo.save(WordEntry("record", [Sense("n.", "记录；唱片\n保留显式换行"), Sense("v.", "记录；录制")],
                  [RelatedWord("recorder", [Sense("n.", "录音机"), Sense("其他", "关联词的第二条释义")])]))
        for index in range(1, 64):
            repo.save(WordEntry(f"vocabulary-{index:02}", [Sense("adj.", "词汇示例 " + str(index))]))
        for index in range(3):
            repo.save(WordEntry(f"learned-{index}", [Sense("interj.", "已记下的词")], learned=True))
    window = MainWindow(repo)
    window.setWindowTitle("kkMemorizeVocabulary — 排序搜索验收（隔离）")
    window.show()
    report = {"platform": app.platformName(), "scale": window.devicePixelRatioF(), "checks": [], "ok": False}

    def check():
        try:
            window.set_global_hidden(False)
            QTest.qWait(60)
            window.grab().save(str(args.data_dir / "normal.png"))
            table = window.table
            first = table.model().rows[0].entry
            origin = dict(table.action_rects(table.visualRect(table.model().index(0, 4))))["drag"].center()
            starts = [i for i, row in enumerate(table.model().rows) if row.first]
            destination = QPoint(origin.x(), table.rowViewportPosition(starts[2]) + 24)
            QTest.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=origin)
            QTest.mouseMove(table.viewport(), destination, delay=30)
            QTest.qWait(75)
            assert table._drag_active
            window.grab().save(str(args.data_dir / "drag-preview.png"))
            QTest.qWait(100)
            target = table._drag_target
            QTest.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=destination)
            assert repo.list_entries()[target].id == first.id
            window.grab().save(str(args.data_dir / "drag-saved.png"))
            report["checks"].append("native_group_drag_and_persistence")
            window.search_box.setText(" 录音 ")
            QTest.qWait(50)
            matches = window._filter_entries(repo.list_entries())
            assert [entry.text for entry in matches] == ["record"]
            assert not table.reorder_enabled and not table.shuffle_button.isEnabled()
            write_entries(args.data_dir / "filtered.json", matches)
            window.grab().save(str(args.data_dir / "filtered.png"))
            report["checks"].append("related_translation_search_and_filtered_export")
            window.set_filter("all")
            assert not table.show_drag_handle and not table.shuffle_button.isVisible()
            window.search_box.clear()
            window.grab().save(str(args.data_dir / "all.png"))
            window.set_filter("learned")
            assert table.show_drag_handle and not table.shuffle_button.isVisible()
            window.grab().save(str(args.data_dir / "learned.png"))
            report["checks"].append("category_controls")
            window.set_filter("pending")
            window.resize(860, 640)
            QTest.qWait(80)
            controls = [*window.filter_buttons.values(), window.settings_button, window.search_box,
                        window.global_hide_button, window.import_button, window.export_button]
            report["toolbar"] = [{"name": control.accessibleName() or control.text(),
                                  "x": control.x(), "width": control.width()} for control in controls]
            assert all(left.geometry().right() < right.x() for left, right in zip(controls, controls[1:]))
            assert controls[-1].geometry().right() < controls[-1].parentWidget().width()
            assert table.columnWidth(3) >= 100
            window.grab().save(str(args.data_dir / "minimum.png"))
            report["checks"].append("minimum_size_toolbar_and_translation_width")
            report["ok"] = True
        except Exception:
            report["error"] = traceback.format_exc()
        (args.data_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.hold or not report["ok"]:
            window._baseline = window.form_value()
            window.close()
            app.exit(0 if report["ok"] else 1)

    QTimer.singleShot(250, check)
    try:
        return app.exec()
    finally:
        repo.close()


if __name__ == "__main__":
    raise SystemExit(main())

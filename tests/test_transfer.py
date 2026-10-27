from dataclasses import replace
import json
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QMessageBox

from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.storage import Repository
from vocabulary.transfer import parse_entries, read_entries, serialize_entries, write_entries


def complete(text="record", *, learned=True, important=False):
    return WordEntry(text, [Sense("n.", "记录\n第二行"), Sense("v.", "记录；录制")],
                     [RelatedWord("recorder", [Sense("n.", "录音机"), Sense("其他", "关联释义")]),
                      RelatedWord("record", [Sense("v.", "允许与主词同名")])], important, learned)


def by_text(repo, text):
    return next(entry for entry in repo.list_entries() if entry.text.casefold() == text.casefold())


def test_json_roundtrip_complete_entries_and_restart(repo, tmp_path):
    source = [repo.save(complete()), repo.save(complete("other", learned=False, important=True))]
    path = tmp_path / "词条.json"
    write_entries(path, repo.list_entries())
    assert "第二行" in path.read_text(encoding="utf-8")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "id" not in document["words"][0]
    target_path = tmp_path / "restored.sqlite3"
    target = Repository(target_path)
    try:
        result = target.import_entries(read_entries(path))
        assert result.inserted == 2 and not result.conflicts
    finally:
        target.close()
    target = Repository(target_path)
    try:
        assert serialize_entries(target.list_entries()) == serialize_entries(repo.list_entries())
    finally:
        target.close()


@pytest.mark.parametrize("change", [
    lambda d: d.update(version=3),
    lambda d: d.update(version=True),
    lambda d: d.update(format="unknown"),
    lambda d: d.update(words={}),
    lambda d: d["words"][1].update(learned="false"),
    lambda d: d["words"][1].update(important=1),
    lambda d: d["words"][1].update(text="  "),
    lambda d: d["words"][1].update(senses=[]),
    lambda d: d["words"][1]["senses"][0].update(pos="noun"),
    lambda d: d["words"][1]["senses"][0].update(translation=123),
    lambda d: d["words"][1]["related"][0].update(text=None),
    lambda d: d["words"][1]["related"][0].update(senses=[]),
    lambda d: d["words"][1].pop("learned"),
])
def test_invalid_file_rejected_before_any_write(repo, change):
    document = json.loads(serialize_entries([complete("first"), complete("second")]))
    change(document)
    with pytest.raises(ValidationError):
        repo.import_entries(parse_entries(json.dumps(document)))
    assert repo.list_entries() == []


@pytest.mark.parametrize("content", ["{", "[]", "null", '{"format": "kkMemorizeVocabulary"}'])
def test_malformed_json(content):
    with pytest.raises(ValidationError):
        parse_entries(content)


def test_empty_file_and_utf8_bom(repo, tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(serialize_entries([]), encoding="utf-8-sig")
    assert repo.import_entries(read_entries(path)).inserted == 0


def test_export_write_failure_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "backup.json"
    path.write_text("previous", encoding="utf-8")
    def fail(*args):
        raise OSError("file locked")
    monkeypatch.setattr("vocabulary.transfer.os.replace", fail)
    with pytest.raises(OSError):
        write_entries(path, [complete()])
    assert path.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_import_skips_all_main_duplicates_and_continues(repo):
    original = repo.save(complete())
    incoming = [complete(" RECORD ", learned=False, important=True),
                complete("new"), complete("NEW", learned=False), complete("recorder"),
                complete("record"), complete("last")]
    result = repo.import_entries(incoming)
    assert result.inserted == 3
    assert [entry.text for entry in repo.list_entries()] == ["record", "new", "recorder", "last"]
    assert [item.incoming.text for item in result.conflicts] == ["RECORD", "NEW", "record"]
    assert repo.get(original.id) == original
    assert all(entry.id is None for entry in incoming)


def test_import_insert_transaction_rolls_back(repo):
    repo.connection.execute("""CREATE TRIGGER fail_sense BEFORE INSERT ON senses
        WHEN NEW.translation='FAIL' BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
    invalid = complete("bad")
    invalid.senses[0].translation = "FAIL"
    with pytest.raises(sqlite3.IntegrityError):
        repo.import_entries([complete("first"), invalid])
    assert repo.list_entries() == []


def test_import_replacement_is_complete_and_recreates_at_end(repo):
    first = repo.save(WordEntry("record", [Sense("adj.", "原内容")], important=True))
    second = repo.save(complete("second"))
    incoming = complete("RECORD")
    result = repo.import_entries([incoming])
    repo.replace_imports(result.conflicts)
    adopted = by_text(repo, "record")
    assert adopted.id != first.id
    assert adopted == replace(incoming, id=adopted.id, sort_order=2)
    with pytest.raises(ValueError):
        repo.get(first.id)
    assert [item.id for item in repo.list_entries()] == [second.id, adopted.id]
    assert repo.get(second.id) == second
    reverse = repo.import_entries([complete("record", learned=False, important=True)])
    repo.replace_imports(reverse.conflicts)
    latest = by_text(repo, "record")
    assert latest.id != adopted.id and not latest.learned and latest.important


def test_all_replacements_roll_back_together(repo):
    originals = [repo.save(complete("one", learned=False)), repo.save(complete("two", learned=False))]
    result = repo.import_entries([complete("one"), complete("two")])
    result.conflicts[1].incoming.senses[0].translation = "FAIL"
    repo.connection.execute("""CREATE TRIGGER fail_sense BEFORE INSERT ON senses
        WHEN NEW.translation='FAIL' BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_imports(result.conflicts)
    assert repo.list_entries() == originals


def test_repeated_file_candidates_apply_in_file_order(repo):
    result = repo.import_entries([complete("same"), complete("SAME", learned=False),
                                  complete("Same", important=True)])
    assert result.inserted == 1 and len(result.conflicts) == 2
    repo.replace_imports(result.conflicts)
    entry = repo.list_entries()[0]
    assert entry.text == "Same" and entry.important and entry.learned


def test_conflict_dialog_shows_complete_plain_text_and_immediate_choices(repo, qtbot):
    first, second = repo.save(complete("one")), repo.save(complete("two"))
    result = repo.import_entries([complete("ONE", learned=False, important=True),
                                  complete("TWO", learned=False, important=True)])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    left, right = dialog.cards[0]
    assert left.star.accessibleName() == "非重点" and right.star.accessibleName() == "重点"
    assert left.learned.isVisible() and not right.learned.isVisible()
    assert left.learned.toolTip() == "已记住此单词"
    assert "    record" in left.description.text() and "\n第二行" in left.description.text()
    assert left.description.text().startswith("n. 记录")
    assert "    其他 关联释义" in left.description.text()
    assert "--" not in left.description.text()
    assert "关联词" not in left.description.text()
    qtbot.mouseClick(left.description, Qt.MouseButton.LeftButton)
    assert len(dialog.conflicts) == 1 and repo.get(first.id) == first
    qtbot.mouseClick(dialog.cards[0][1].word, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.replaced_ids == {by_text(repo, "two").id}
    assert repo.get(first.id) == first
    assert not by_text(repo, "two").learned and by_text(repo, "two").important


def test_conflict_dialog_cancel_retains_new_words_and_originals(repo, qtbot):
    original = repo.save(complete())
    result = repo.import_entries([complete("RECORD", learned=False), complete("new")])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.reject()
    assert repo.get(original.id) == original
    assert len(repo.list_entries()) == 2
    assert not dialog.replaced_ids


def test_conflict_dialog_failed_apply_stays_open_for_retry(repo, qtbot, monkeypatch):
    original = repo.save(complete())
    result = repo.import_entries([complete("RECORD", learned=False)])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    original_replace = repo.replace_imports
    def fail(choices):
        raise OSError("locked")
    monkeypatch.setattr(repo, "replace_imports", fail)
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[2]))
    qtbot.mouseClick(dialog.cards[0][1], Qt.MouseButton.LeftButton)
    assert dialog.isVisible() and len(dialog.cards) == 1
    assert repo.get(original.id) == original
    assert "locked" in messages[0]
    monkeypatch.setattr(repo, "replace_imports", original_replace)
    qtbot.mouseClick(dialog.cards[0][1], Qt.MouseButton.LeftButton)
    assert not dialog.isVisible() and not by_text(repo, "record").learned


def test_toolbar_icons_export_current_filter(window, repo, tmp_path, monkeypatch):
    learned = repo.save(complete("learned"))
    pending = repo.save(complete("pending", learned=False))
    window.refresh()
    assert window.import_button.x() > window.global_hide_button.x()
    assert window.export_button.x() > window.import_button.x()
    assert window.import_button.text() == window.export_button.text() == ""
    assert window.import_button.toolTip() == window.import_button.accessibleName() == "导入单词"
    assert window.export_button.toolTip() == window.export_button.accessibleName() == "导出当前页面所有单词"
    assert not window.import_button.icon().isNull() and not window.export_button.icon().isNull()
    path = tmp_path / "all.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    for hidden in (True, False):
        window.set_global_hidden(hidden)
        for filter_name, expected in (("pending", [pending]), ("learned", [learned]), ("all", [pending, learned])):
            window.set_filter(filter_name)
            window.export_words()
            assert serialize_entries(read_entries(path)) == serialize_entries(expected)


def test_window_import_new_first_then_review_and_clear_stale_editor(window, repo, tmp_path, monkeypatch):
    original = repo.save(complete())
    window.reset_form(original)
    path = tmp_path / "import.json"
    write_entries(path, [complete("RECORD", learned=False, important=True), complete("new")])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    def review(dialog):
        assert len(repo.list_entries()) == 2
        assert repo.get(original.id) == original
        assert window.edit_id is None
        dialog.choose_all(True)
        return dialog.result()
    monkeypatch.setattr(ImportConflictDialog, "exec", review)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    window.import_words()
    assert not by_text(repo, "record").learned and by_text(repo, "record").important
    assert "新增 1" in messages[0] and "覆盖 1" in messages[0]
    assert window.filter_buttons["all"].text() == "全部  2"


def test_window_invalid_file_keeps_draft_and_data(window, repo, tmp_path, monkeypatch):
    original = repo.save(complete())
    window.blocks[0].word.setText("未保存")
    draft = window.form_value()
    path = tmp_path / "invalid.json"
    path.write_text("broken", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[2]))
    window.import_words()
    assert repo.list_entries() == [original] and window.form_value() == draft
    assert messages


def test_window_reports_committed_choices_even_when_remaining_dialog_is_closed(window, repo, tmp_path, monkeypatch):
    first = repo.save(complete("one", learned=False))
    second = repo.save(complete("two", learned=False))
    path = tmp_path / "partial.json"
    write_entries(path, [complete("ONE"), complete("TWO")])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    def review(dialog):
        dialog.choose(dialog.conflicts[0], True)
        assert by_text(repo, "one").learned
        assert len(dialog.conflicts) == 1
        dialog.reject()
        return dialog.result()
    monkeypatch.setattr(ImportConflictDialog, "exec", review)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    window.import_words()
    assert by_text(repo, "one").learned and not repo.get(second.id).learned
    assert "2 条重复记录" in messages[0] and "覆盖 1" in messages[0]


def test_window_import_respects_dirty_cancel(window, repo, tmp_path, monkeypatch):
    window.blocks[0].word.setText("未保存")
    path = tmp_path / "import.json"
    write_entries(path, [complete()])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    monkeypatch.setattr(window, "resolve_dirty", lambda: False)
    window.import_words()
    assert not repo.list_entries() and window.blocks[0].word.text() == "未保存"


def test_cancel_file_pickers_does_not_change_form(window, repo, monkeypatch):
    window.blocks[0].word.setText("draft")
    draft = window.form_value()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    window.import_words()
    window.export_words()
    assert window.form_value() == draft and repo.list_entries() == []

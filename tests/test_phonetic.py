from copy import deepcopy
from datetime import date
import json
import sqlite3

import pytest
from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt
from PySide6.QtWidgets import QApplication

from vocabulary import migration
from vocabulary.domain import RelatedWord, Sense, ValidationError, WordEntry
from vocabulary.import_dialog import entry_description
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_study_backup, serialize_study_backup
from vocabulary.transfer import parse_entries, serialize_entries


def word(text="record"):
    return WordEntry(text, [Sense("n.", "记录"), Sense("v.", "录制")],
                     [RelatedWord("recorder", [Sense("n.", "录音机")], "/rɪˈkɔːdə/")],
                     phonetic="/ˈrekɔːd/")


def test_phonetic_roundtrip_empty_trimmed_reopen_and_study_backup(repo):
    draft = word()
    draft.phonetic = "  /ˈrekɔːd/  "
    draft.related[0].phonetic = " /rɪˈkɔːdə/ "
    saved = repo.save(draft)
    assert saved.phonetic == "/ˈrekɔːd/"
    assert saved.related[0].phonetic == "/rɪˈkɔːdə/"
    assert repo.save(WordEntry("empty", [Sense("n.", "空")])).phonetic == ""
    with_content = parse_entries(serialize_entries([saved]))[0]
    assert with_content.phonetic == saved.phonetic and with_content.related == saved.related
    repo.prepare_study(StudyContext(date(2026, 11, 5), False))
    backup = repo.study_backup()
    restored = parse_study_backup(serialize_study_backup(backup))
    assert serialize_study_backup(restored) == serialize_study_backup(backup)
    reopened = Repository(repo.path)
    try:
        assert reopened.get(saved.id) == saved
    finally:
        reopened.close()


@pytest.mark.parametrize("related", [False, True])
@pytest.mark.parametrize("value", [None, 3, [], True])
def test_phonetic_import_requires_text(repo, related, value):
    document = json.loads(serialize_entries([word()]))
    target = document["words"][0]
    if related:
        target = target["related"][0]
    target["phonetic"] = value
    with pytest.raises(ValidationError, match="音标"):
        repo.import_entries(parse_entries(json.dumps(document)))
    assert not repo.list_entries()


@pytest.mark.parametrize("related", [False, True])
def test_phonetic_only_conflict_is_visible_and_replacement_rolls_back(repo, related):
    original = repo.save(word())
    incoming = deepcopy(original)
    (incoming.related[0] if related else incoming).phonetic = "/changed/"
    result = repo.import_entries([incoming])
    assert len(result.conflicts) == 1
    assert "/changed/" in entry_description(result.conflicts[0].incoming)
    repo.connection.execute("""CREATE TRIGGER fail_phonetic BEFORE INSERT ON senses
        BEGIN SELECT RAISE(ABORT,'failed'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_imports(result.conflicts)
    assert repo.get(original.id) == original


def test_v7_phonetic_migration_preserves_data_and_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(path)
    for name in migration.MIGRATIONS[:7]:
        raw.executescript(migration.read_migration(name))
    raw.executescript("""
        INSERT INTO words(id,text,normalized_text,sort_order) VALUES (1,'old','old',12);
        INSERT INTO related_words(id,word_id,text,position) VALUES (1,1,'child',0);
        INSERT INTO senses(word_id,pos,translation,position) VALUES (1,'n.','旧词',0);
        INSERT INTO senses(related_id,pos,translation,position) VALUES (1,'v.','关联',0);
        INSERT INTO memorize_vocabulary_17day(word_id,group_number) VALUES (1,8);
        PRAGMA user_version=7;
    """)
    raw.close()
    read = migration.read_migration
    with monkeypatch.context() as patch:
        patch.setattr(migration, "read_migration", lambda name: read(name) + "SELECT * FROM missing;")
        with pytest.raises(sqlite3.OperationalError):
            Repository(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 7
    assert "phonetic" not in [r[1] for r in raw.execute("PRAGMA table_info(words)")]
    assert "phonetic" not in [r[1] for r in raw.execute("PRAGMA table_info(related_words)")]
    raw.close()
    repo = Repository(path)
    try:
        saved = repo.get(1)
        assert saved.phonetic == saved.related[0].phonetic == ""
        assert saved.text == "old" and saved.sort_order == 12
        assert saved.related[0].senses == [Sense("v.", "关联")]
        assert repo.connection.execute("SELECT group_number FROM memorize_vocabulary_17day").fetchone()[0] == 8
    finally:
        repo.close()


def test_form_phonetics_save_and_overwrite_list_while_preserving_progress(window, repo):
    saved = repo.save(word())
    repo.prepare_study(StudyContext(date(2026, 11, 5), False))
    progress = repo.study_backup().records
    window.reset_form(saved)
    block = window.blocks[0]
    block.phonetic.setText(" /draft/ ")
    window.blocks[1].phonetic.setText("/child draft/")
    current = repo.get(saved.id)
    current.text, current.phonetic = "list rename", "/list/"
    repo.save(current)
    repo.set_flag(saved.id, "learned", True)
    repo.batch_update_sort_orders({saved.id: 70})
    assert window._save_form()
    result = repo.get(saved.id)
    assert result.text == "record" and result.phonetic == "/draft/"
    assert result.related[0].phonetic == "/child draft/"
    assert result.learned and result.sort_order == 70
    assert repo.study_backup().records == progress


@pytest.mark.parametrize("page", ["pending", "all", "learned", "study17"])
@pytest.mark.parametrize("empty_main", [False, True])
def test_hover_and_keyboard_show_whole_word_groups(window, repo, qtbot, monkeypatch, page, empty_main):
    draft = word()
    if empty_main:
        draft.phonetic = ""
    draft.related.append(RelatedWord("silent", [Sense("n.", "无音标")]))
    first, second, third = repo.save(draft), repo.save(word("another")), repo.save(word("unrelated"))
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    if page == "learned":
        for entry in (first, second, third):
            repo.set_flag(entry.id, "learned", True)
    window.set_filter(page)
    table = window.current_table()
    if page == "study17":
        for entry in (first, second, third):
            table.set_entry_revealed(entry.id, True)
    else:
        for entry in (first, second, third):
            window.reveal(entry.id, True)
    model = table.model()

    def group_visible(entry, expected):
        for number, row in enumerate(model.rows):
            if row.entry.id != entry.id or row.sense != 0:
                continue
            index = model.index(number, 1)
            assert table.phonetic_visible(index) == expected
            layout = table.delegate.phonetic_layout(index, table.visualRect(index), table.font())
            assert (layout is not None) == (expected and bool(row.word.phonetic))

    # Selecting the main word and hovering another group's child shows both full groups.
    table.select_entry(first.id)
    other = table._entry_index(second.id, owner=0, column=3)
    qtbot.mouseMove(table.viewport(), table.visualRect(other).center())
    group_visible(first, True)
    group_visible(second, True)
    group_visible(third, False)
    # Even a child with no phonetic activates all other phonetics in its group.
    table.setCurrentIndex(table._entry_index(first.id, owner=1))
    sense = table._entry_index(first.id, sense=1, column=3)
    qtbot.mouseMove(table.viewport(), table.visualRect(sense).center())
    group_visible(first, True)
    group_visible(second, False)
    QApplication.sendEvent(table.viewport(), QEvent(QEvent.Type.Leave))
    group_visible(first, True)
    group_visible(second, False)
    table.setCurrentIndex(QModelIndex())
    for entry in (first, second, third):
        group_visible(entry, False)


def test_long_word_hides_phonetic_and_keeps_complete_word_click_region(window, repo, qtbot):
    entry = word("verylongword" * 12)
    entry.phonetic *= 10
    entry = repo.save(entry)
    window.refresh()
    table = window.table
    table.setCurrentIndex(window.model.index(0, 1))
    index = table.currentIndex()
    rect = table.visualRect(index)
    _, text, word_area = table.delegate.word_layout(index, rect, table.font())
    assert text.endswith("…")
    assert table.delegate.phonetic_layout(index, rect, table.font()) is None
    assert table.delegate.word_tooltip(index) == entry.text + '    ' + entry.phonetic
    assert table.delegate.word_hit(index, word_area.center())
    copies = []
    table.copyRequested.connect(copies.append)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=word_area.center().toPoint())
    qtbot.wait(QApplication.doubleClickInterval() + 20)
    assert copies == [entry.text]

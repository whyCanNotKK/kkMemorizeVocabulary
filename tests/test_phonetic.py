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
def test_hover_and_keyboard_show_independent_phonetics(window, repo, qtbot, monkeypatch, page):
    first, second = repo.save(word()), repo.save(word("another"))
    monkeypatch.setattr(StudyContext, "now", classmethod(lambda cls: StudyContext(date(2026, 11, 5), False)))
    if page == "learned":
        for entry in (first, second):
            repo.set_flag(entry.id, "learned", True)
    window.set_filter(page)
    table = window.current_table()
    if page == "study17":
        table.set_entry_revealed(first.id, True)
        table.set_entry_revealed(second.id, True)
    else:
        window.reveal(first.id, True)
        window.reveal(second.id, True)
    model = table.model()
    main = model.index(0, 1)
    child = model.index(2, 1)
    other = model.index(3, 1)
    # Keyboard navigation selects one word, mouse position reveals another.
    table.select_entry(first.id)
    qtbot.mouseMove(table.viewport(), table.visualRect(other).center())
    assert table.phonetic_visible(main) and table.phonetic_visible(other)
    assert not table.phonetic_visible(child)
    assert table.delegate.phonetic_layout(main, table.visualRect(main), table.font())[1] == first.phonetic
    # Hovering another sense of the same word reveals its word-name row.
    table.setCurrentIndex(child)
    qtbot.mouseMove(table.viewport(), table.visualRect(model.index(1, 3)).center())
    assert table.phonetic_visible(main) and table.phonetic_visible(child)
    assert not table.phonetic_visible(other)
    QApplication.sendEvent(table.viewport(), QEvent(QEvent.Type.Leave))
    assert not table.phonetic_visible(main) and table.phonetic_visible(child)
    table.setCurrentIndex(QModelIndex())
    assert not table.phonetic_visible(child)


def test_long_word_and_phonetic_render_separately_but_share_word_click_region(window, repo, qtbot):
    entry = word("verylongword" * 12)
    entry.phonetic *= 10
    entry = repo.save(entry)
    window.refresh()
    table = window.table
    table.setCurrentIndex(window.model.index(0, 1))
    index = table.currentIndex()
    rect = table.visualRect(index)
    _, text, word_area = table.delegate.word_layout(index, rect, table.font())
    _, phonetic, phonetic_area = table.delegate.phonetic_layout(index, rect, table.font())
    assert text.endswith("…") and phonetic.endswith("…")
    assert word_area.right() < phonetic_area.left()
    assert table.delegate.word_hit(index, phonetic_area.center())
    copies = []
    table.copyRequested.connect(copies.append)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=phonetic_area.center().toPoint())
    qtbot.wait(QApplication.doubleClickInterval() + 20)
    assert copies == [entry.text]

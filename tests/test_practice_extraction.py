import pytest

from scripts.extract_practice_words import RULES_PATH, extract, load_rules, main
from vocabulary.transfer import parse_entries, serialize_entries


def convert(content):
    return extract(content, load_rules(RULES_PATH))


def test_repeated_practice_merges_new_relations_without_losing_other_owners():
    entries, report = convert(
        "intricately intricately intricate intricate\n"
        "INTRICATELY intricate intricacy\n"
        "intricate intricately\n"
        "other intricate intricate\n"
    )
    assert [e.text for e in entries] == ["intricately", "intricate", "other"]
    assert [[r.text for r in e.related] for e in entries] == [
        ["intricate", "intricacy"], ["intricately"], ["intricate"]]
    assert report["entries"][0]["source_lines"] == [1, 2]


def test_longest_phrase_matching_handles_any_spacing_and_related_phrases():
    entries, _ = convert(
        "account for   account for\n"
        "ACCOUNT  FOR account for\n"
        "high vis high vis jacket high vis jacket\n"
        "strike up strike  up\n"
        "presentation presentation power point presentation\n"
        "in charge of\n"
    )
    assert [e.text for e in entries] == [
        "account for", "high vis", "strike up", "presentation", "in charge of"]
    assert not entries[0].related and not entries[2].related
    assert [r.text for r in entries[1].related] == ["high vis jacket"]
    assert [r.text for r in entries[3].related] == ["power point presentation"]


def test_example_exclusions_cleanup_and_spelling_are_auditable():
    entries, report = convert(
        "manipulate manipulate Faker's manipulate as good as mine\n"
        "warrant warrant the court warranted police to arrest you.\n"
        "sense sense It's make no sense sense\n"
        "vibration vibration] viration\n"
        "herbivore herb\\ herb\n"
        "self-esteem self-esteem\n"
    )
    assert all(not e.related for e in entries[:3])
    assert [r.text for r in entries[3].related] == ["viration"]
    assert [r.text for r in entries[4].related] == ["herb"]
    assert entries[5].text == "self-esteem"
    assert [item["line"] for item in report["excluded_fragments"]] == [1, 2, 3]
    assert len(report["punctuation_cleanup"]) == 2


def test_generated_entries_import_and_duplicate_detection(repo):
    entries, _ = convert("account for account for\nconserve conservation\nCONSERVE preserve\n")
    parsed = parse_entries(serialize_entries(entries))
    assert repo.import_entries(parsed).inserted == 2
    saved = repo.list_entries()
    assert [e.text for e in saved] == ["account for", "conserve"]
    assert [r.text for r in saved[1].related] == ["conservation", "preserve"]
    for entry in saved:
        assert entry.important and not entry.learned
        for word in [entry, *entry.related]:
            assert [(s.pos, s.translation) for s in word.senses] == [("n.", "1")]
    assert len(repo.import_entries(parsed).conflicts) == 2


def test_cli_rejects_overwriting_source(tmp_path, monkeypatch):
    source = tmp_path / "practice.txt"
    source.write_text("original", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["extract", str(source), "-o", str(source)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert source.read_text(encoding="utf-8") == "original"

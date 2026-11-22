"""Source-faithful PDF conversion and real importer integration."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
from tools.pdf_vocabulary import extract
from vocabulary.domain import Sense
from vocabulary.storage import Repository
from vocabulary.transfer import parse_entries, read_entries, serialize_entries


def row(number, raw, page=1):
    return extract.SourceRow(number, page, raw)


def codes(items):
    return {item["code"] for item in items}


def test_wrapped_rows_standalone_number_and_word_internal_slashes():
    pages = [
        "序号 单词 音标 中文意思\r\n"
        "0 U.K./UK(缩)“＝”UnitedKingdom /juːˈkeɪ/ n.英国\r\n"
        "1 contemporary\r\n/kənˈtempərərɪ;(US)kə\r\nnˈtempərerɪ/\r\n"
        "adj.&n.同时代的&同代人",
        "2\r\nVCD“＝\r\n”versatilecompactdis\r\n// n.影碟光盘",
    ]
    rows = extract.table_rows(pages)
    assert [(r.number, r.page) for r in rows] == [(0, 1), (1, 1), (2, 2)]
    entries, report = extract.convert_rows(rows)
    assert [e.text for e in entries] == ["U.K./UK", "contemporary", "VCD"]
    assert entries[1].phonetic == "/kənˈtempərerɪ/"
    assert entries[1].senses == [Sense("adj.", "同时代的"), Sense("n.", "同代人")]
    assert entries[2].phonetic == ""
    assert report["records"][2]["raw"] == rows[2].raw


@pytest.mark.parametrize("source, expected, rank, warnings", [
    ("/ˈfɔːkɑːst;(US)ˈfɔrkæst/", "/ˈfɔrkæst/", 1, {"multiple_pronunciations"}),
    ("/ˈstrɔːbərɪ;(US)-berɪ/", "/ˈstrɔːbərɪ/", 0, {"us_fragment_fallback"}),
    ("/wiːkˈend,ˈwiːkend/", "/wiːkˈend/", 0, {"multiple_pronunciations"}),
    ("/pen-frɪend/", "/pen-frɪend/", 0, set()),
    ("//", "", -1, {"missing_complete_phonetic"}),
    ("/skuːl-/", "", -1, {"missing_complete_phonetic"}),
    ("/glæns/glɑːns/", "/glæns/", 0, {"malformed_phonetic"}),
])
def test_single_phonetic_american_fragments_and_compounds(source, expected, rank, warnings):
    issues = []
    assert extract.select_phonetic(source, issues) == (expected, rank)
    assert codes(issues) >= warnings


def test_separate_shared_and_aligned_senses_without_semantic_guessing():
    issues = []
    assert extract.parse_senses("adj&v.清晰的&清除", issues) == [
        Sense("adj.", "清晰的"), Sense("v.", "清除")]
    assert extract.parse_senses("n./v.通道,入径,存取(计算机文件)", issues) == [
        Sense("n.", "通道,入径,存取(计算机文件)"),
        Sense("v.", "通道,入径,存取(计算机文件)")]
    assert extract.parse_senses("n.光，光亮vt.点（火），点燃a.明亮的", issues) == [
        Sense("n.", "光，光亮"), Sense("v.", "点（火），点燃"), Sense("adj.", "明亮的")]
    assert "shared_translation" in codes(issues)


def test_unsupported_missing_pos_notes_and_truncated_definition():
    issues = []
    assert extract.parse_senses("num.十亿", issues) == [Sense("else", "十亿")]
    assert extract.parse_senses("vn.旅行者", issues) == [Sense("else", "旅行者")]
    assert extract.parse_senses("北冰洋", issues) == [Sense("else", "北冰洋")]
    assert extract.parse_senses("n导弹", issues) == [Sense("n.", "导弹")]
    assert extract.parse_senses("(复teeth)n.牙齿", issues) == [Sense("n.", "牙齿")]
    assert extract.parse_senses("n.(pl.)牙齿", issues) == [Sense("n.", "(pl.)牙齿")]
    assert extract.parse_senses("vt.放（风", issues) == [Sense("v.", "放（风")]
    assert codes(issues) >= {"unknown_pos", "missing_pos", "pos_punctuation",
                            "definition_notes", "unbalanced_definition"}


@pytest.mark.parametrize("source, expected", [
    ("(缩)“＝”televisionn.电视机", [Sense("n.", "电视机")]),
    ("“＝”telephonev.打电话n.电话", [Sense("v.", "打电话"), Sense("n.", "电话")]),
    ("mm(缩)“＝”millimetre n.毫米", [Sense("n.", "毫米")]),
])
def test_explicit_alias_annotations_do_not_swallow_trailing_pos(source, expected):
    issues = []
    assert extract.parse_senses(source, issues) == expected
    assert "definition_notes" in codes(issues)


def test_inflections_aliases_and_internal_slashes_are_only_source_notes():
    for source, expected in [
        ("speak(spoke,spoken)", "speak"), ("colour(美color)", "colour"),
        ("a(an)", "a"), ("theNorth(South)Pole", "theNorthPole"),
        ("can(could)can't“＝”cannotmodal", "can"),
        ("fridge“＝”refrigerator", "fridge"),
        ("statesman/woman(pl.statesmen/women)", "statesman/woman"),
    ]:
        issues = []
        assert extract.normalize_head(source, issues) == expected
        assert "head_notes_removed" in codes(issues)
    assert extract.normalize_head("theAtlanticOcean", []) == "theAtlanticOcean"


def test_merge_casefold_order_senses_and_prefer_complete_us_across_rows():
    entries, report = extract.convert_rows([
        row(0, "March /mɑːtʃ/ n.3月"),
        row(1, "record /ˈrekɔːd/ v.录制"),
        row(2, "march /mɑːtʃ/ n.游行"),
        row(4, "record /ˈrekɔːd;(US)ˈrekərd/ n.&v.记录&录制"),
        row(5, "record /other;(US)other/ n.记录"),
    ])
    assert [e.text for e in entries] == ["March", "record"]
    assert entries[0].senses == [Sense("n.", "3月"), Sense("n.", "游行")]
    assert entries[1].senses == [Sense("v.", "录制"), Sense("n.", "记录")]
    assert entries[1].phonetic == "/ˈrekərd/"
    assert report["statistics"]["missing_source_numbers"] == [3]
    assert report["statistics"]["merged_rows"] == 3
    assert report["entries"][1]["source_rows"] == [
        {"page": 1, "number": 1}, {"page": 1, "number": 4}, {"page": 1, "number": 5}]
    assert all(not e.related and not e.learned and not e.important for e in entries)


def test_source_mismatches_preserved_damaged_heads_audited_only():
    entries, report = extract.convert_rows([
        row(0, "television /ˈtelɪɡrɑːf;(US)-ɡræf/ n.电视"),
        row(1, "speak(spoke,spoken) /ˈspærəʊ/ v.说"),
        row(2, "#VALUE! #VALUE! a.无声的"),
    ])
    assert entries[0].phonetic == "/ˈtelɪɡrɑːf/"
    assert entries[1].phonetic == "/ˈspærəʊ/"
    assert report["statistics"]["skipped_rows"] == 1
    assert report["statistics"]["issue_counts"]["known_source_issue"] == 2
    assert report["records"][-1]["action"] == "skip"


@pytest.mark.parametrize("pages", [
    ["Group 1 a art.一"],
    ["0 word /w/ n.词", "0 other /o/ n.其他"],
    ["Unexpected material\n0 word /w/ n.词"],
])
def test_unrecognized_table_or_nonincreasing_number_is_not_silently_skipped(pages):
    with pytest.raises(ValueError):
        extract.table_rows(pages)


def test_cli_validates_before_replacing_existing_deliverables(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"read-only source")
    output = tmp_path / "words.json"
    raw = output.with_suffix(".raw.txt")
    report = output.with_suffix(".report.json")
    for path in (output, raw, report):
        path.write_text("keep previous", encoding="utf-8")
    monkeypatch.setattr(extract, "read_pdf", lambda *args: (["0 missing-phonetic"], 1))
    with pytest.raises(SystemExit) as error:
        extract.main([str(source), "-o", str(output), "--last-page", "1"])
    assert error.value.code == 1
    assert all(p.read_text(encoding="utf-8") == "keep previous" for p in (output, raw, report))
    assert source.read_bytes() == b"read-only source"
    with pytest.raises(ValueError, match="不同路径"):
        extract.validate_paths(source, source, raw, report)
    with pytest.raises(ValueError, match="工具源文件"):
        extract.validate_paths(source, Path(extract.__file__), raw, report)


def test_real_pdf_cli_and_real_database_import_restart_roundtrip(tmp_path, qapp):
    source = tmp_path / "structured.pdf"
    # A standard-font PDF fixture keeps this test independent of host fonts,
    # including Qt's offscreen platform on Windows.
    streams = [
        b"BT /F1 12 Tf 20 750 Td (0 record /rek/ n.recording) Tj "
        b"0 -30 Td (1 record /rek/ v.record) Tj ET",
        b"BT /F1 12 Tf 20 750 Td (Group 1 appendix should be excluded) Tj ET",
    ]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(streams[0])).encode() + b" >>\nstream\n" + streams[0] + b"\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
        b"<< /Length " + str(len(streams[1])).encode() + b" >>\nstream\n" + streams[1] + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 8\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    source.write_bytes(data)
    source_bytes = source.read_bytes()
    output = tmp_path / "output" / "words.json"
    assert extract.main([str(source), "-o", str(output), "--last-page", "1"]) == 0
    entries = read_entries(output)
    assert len(entries) == 1
    assert entries[0].senses == [Sense("n.", "recording"), Sense("v.", "record")]
    assert parse_entries(serialize_entries(entries)) == entries
    report = json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["statistics"]["excluded_pages"] == 1
    assert report["statistics"]["source_rows"] == 2
    assert report["source_sha256"]
    assert "Group 1" not in output.with_suffix(".raw.txt").read_text(encoding="utf-8")
    assert source.read_bytes() == source_bytes
    database_path = tmp_path / "isolated.sqlite3"
    repository = Repository(database_path)
    try:
        imported = repository.import_entries(entries)
        assert imported.inserted == 1 and not imported.conflicts
    finally:
        repository.close()
    repository = Repository(database_path)
    try:
        assert serialize_entries(repository.list_entries()) == serialize_entries([
            # Repository persists a positive sort_order; compare with that field.
            replace(entries[0], sort_order=1)
        ])
    finally:
        repository.close()

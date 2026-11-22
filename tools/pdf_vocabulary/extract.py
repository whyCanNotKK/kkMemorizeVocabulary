"""Convert the numbered table in the supplied Gaokao vocabulary PDF to JSON v3.

The input is a text PDF, not a scanned book. No OCR, dictionary, or network is
used. All source rows and conversion decisions are retained in the audit file.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vocabulary.domain import Sense, WordEntry, clean_entry
from vocabulary.transfer import parse_entries, serialize_entries, write_text_atomically


ROW_START = re.compile(r"(?m)^(\d+)(?:[ \t]+|\r?\n)")
POS_MAP = {
    "n": "n.", "v": "v.", "vt": "v.", "vi": "v.",
    "a": "adj.", "adj": "adj.", "ad": "adv.", "adv": "adv.",
    "prep": "prep.", "pron": "pron.", "conj": "conj.",
    "int": "interj.", "interj": "interj.",
}
POS_MARK = re.compile(
    r"(?<![A-Za-z])([a-z]+)(\.+|,(?=[\u3400-\u9fff])|(?=[&/\u3400-\u9fff（(]))"
)
ISSUE_LABELS = {
    "damaged_head": "原词头损坏，未导入；不猜测恢复",
    "head_notes_removed": "词头的变形/别名说明仅保存在来源报告，不生成关联词",
    "unbalanced_head": "原词头括号不完整，说明已移至报告",
    "unusual_head": "词头含非常规标记，保留可识别原文，请人工检查",
    "unknown_pos": "不支持或无法确认的原词性转换为else",
    "missing_pos": "没有可明确识别的词性，使用else并保留原文",
    "pos_punctuation": "词性标点缺失或异常，按可明确识别的标记规范化",
    "definition_notes": "释义前的变形或别名说明保存在报告",
    "unassigned_prefix": "首个词性之前有未归属文本，使用else保留",
    "shared_translation": "原文未明确逐项分配释义，各词性共享完整原文",
    "unbalanced_definition": "原释义括号不完整，可能截断；保留原文",
    "empty_translation": "原文没有该词性的释义正文，保留空释义",
    "us_fragment_fallback": "美式音标只有片段，回退原文完整音标，不猜测展开",
    "multiple_pronunciations": "存在多个音标，按完整美式优先、同级首次规则选择一个",
    "malformed_phonetic": "原音标分隔异常，保留来源并检查所选读音",
    "missing_complete_phonetic": "没有可选的完整音标，导入音标留空",
    "duplicate_merged": "重复词合并至首次出现的词条，释义有序去重",
    "case_collision": "拼写仅大小写不同，按程序同名规则合并，保留首次拼写",
    "phonetic_conflict": "重复词的音标不同，按完整美式优先、同级首次规则选择",
    "known_source_issue": "已在源资料中发现的疑似错误，仅报告，未纠正",
}


@dataclass(frozen=True)
class SourceRow:
    number: int
    page: int
    raw: str


def join_wraps(text: str) -> str:
    """Remove PDF visual wraps, retaining existing spaces inside a line."""
    return re.sub(r"[ \t]*\r?\n[ \t]*", "", text).strip()


def issue(items: list[dict], code: str, detail: str = "") -> None:
    item = {"code": code, "message": ISSUE_LABELS[code]}
    if detail:
        item["detail"] = detail
    if item not in items:
        items.append(item)


def unbalanced(text: str) -> bool:
    stack = []
    for char in text:
        if char in "(（":
            stack.append(char)
        elif char in ")）":
            if not stack:
                return True
            stack.pop()
    return bool(stack)


def table_rows(pages: list[str], first_page: int = 1) -> list[SourceRow]:
    rows = []
    previous = -1
    for page_number, text in enumerate(pages, first_page):
        matches = list(ROW_START.finditer(text))
        if not matches:
            raise ValueError(f"第{page_number}页未发现编号主表；请检查页码范围，不能静默跳过。")
        prefix = text[:matches[0].start()].strip()
        if prefix and re.sub(r"\s+", "", prefix) != "序号单词音标中文意思":
            raise ValueError(f"第{page_number}页存在无法归属的表前文本：{prefix!r}")
        for index, match in enumerate(matches):
            number = int(match[1])
            if number <= previous:
                raise ValueError(f"第{page_number}页原编号{number}未递增，停止转换以避免丢行。")
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            raw = text[match.end():end].strip()
            if not raw:
                raise ValueError(f"第{page_number}页原编号{number}没有内容。")
            rows.append(SourceRow(number, page_number, raw))
            previous = number
    return rows


def split_fields(row: SourceRow) -> tuple[str, str, str]:
    # Whitespace before the opening slash matters: U.K./UK and inflection
    # annotations contain slashes which are not phonetic delimiters.
    start = re.search(r"\s+/", row.raw)
    if start is None:
        raise ValueError(f"第{row.page}页原编号{row.number}缺少可识别的音标列：{row.raw!r}")
    head = join_wraps(row.raw[:start.start()])
    rest = row.raw[start.end():]
    closing = rest.find("/")
    if closing < 0:
        raise ValueError(f"第{row.page}页原编号{row.number}音标没有闭合斜杠。")
    phonetic = rest[:closing]
    tail = rest[closing + 1:]
    # A handful of source cells use /first/second/ instead of /first,second/.
    # Do not consume n./v. or slashes embedded in Chinese definitions/notes.
    while alternate := re.match(r"([^/\s\u3400-\u9fff.()（）]+)/", tail):
        phonetic += "/" + alternate[1]
        tail = tail[alternate.end():]
    return head, "/" + phonetic + "/", join_wraps(tail)


def normalize_head(raw: str, items: list[dict]) -> str:
    text = raw.strip()
    first = re.search(r"[（(]", text)
    if first:
        suffix = re.search(r"[)）]([A-Z][a-z]+)$", text)
        # Keep the proper-name suffix in theNorth(South)Pole. The other
        # bracket tails are word-form/alias annotations, not new headwords.
        text = text[:first.start()] + (suffix[1] if suffix else "")
        issue(items, "head_notes_removed", raw)
        if unbalanced(raw):
            issue(items, "unbalanced_head", raw)
    alias = re.search(r"[“\"]?[＝=][”\"]?", text)
    if alias:
        text = text[:alias.start()].rstrip("“\" ")
        issue(items, "head_notes_removed", raw)
    if not text or not re.search(r"[A-Za-z]", text):
        raise ValueError(f"无法识别词头：{raw!r}")
    if re.search(r"[^A-Za-z0-9 ./'’\-]", text):
        issue(items, "unusual_head", text)
    return text.strip()


def select_phonetic(raw: str, items: list[dict]) -> tuple[str, int]:
    phonetic = re.sub(r"\s+", "", raw.strip("/"))
    if "/" in phonetic:
        issue(items, "malformed_phonetic", raw)
    regions = re.split(r"[;；]?\(US\)", phonetic, maxsplit=1)
    base = [p.strip() for p in re.split(r"[,，;；/]", regions[0]) if p.strip()]
    american = ([p.strip() for p in re.split(r"[,，;；/]", regions[1]) if p.strip()]
                if len(regions) == 2 else [])
    def complete(value: str) -> bool:
        # An internal hyphen can join full compound-word pronunciations.
        # Leading/trailing hyphens instead mark abbreviated replacements.
        return bool(value) and not re.search(r"^[-‐‑–—]|[-‐‑–—]$", value)
    valid_us = [p for p in american if complete(p)]
    valid_base = [p for p in base if complete(p)]
    if any(not complete(p) for p in american):
        issue(items, "us_fragment_fallback", raw)
    if len(base) + len(american) > 1:
        issue(items, "multiple_pronunciations", raw)
    if valid_us:
        return f"/{valid_us[0]}/", 1
    if valid_base:
        return f"/{valid_base[0]}/", 0
    issue(items, "missing_complete_phonetic", raw)
    return "", -1


def parse_senses(raw: str, items: list[dict]) -> list[Sense]:
    if unbalanced(raw):
        issue(items, "unbalanced_definition", raw)
    # Aliases sometimes touch the POS label: “＝”televisionn.电视机.
    # The explicit equals annotation and trailing marker give a structural
    # boundary; retain the entire removed annotation in the audit record.
    if "＝" in raw or "=" in raw:
        alias_mark = re.search(r"([A-Za-z]+)\.(?=[\u3400-\u9fff（(])", raw)
        if alias_mark and any(c in raw[:alias_mark.start()] for c in "＝="):
            token = alias_mark[1]
            suffix = next((p for p in sorted(POS_MAP, key=len, reverse=True)
                           if token.endswith(p)), None)
            if suffix and token != suffix:
                boundary = alias_mark.end(1) - len(suffix)
                issue(items, "definition_notes", raw[:boundary])
                raw = raw[boundary:]
    # Only markers outside parenthetical usage/form notes delimit senses.
    depth = 0
    depths = []
    for char in raw:
        depths.append(depth)
        if char in "(（":
            depth += 1
        elif char in ")）":
            depth = max(0, depth - 1)
    marks = [m for m in POS_MARK.finditer(raw)
             if depths[m.start()] == 0 and (m[2] or m[1] in POS_MAP)]
    if not marks:
        issue(items, "missing_pos", raw)
        return [Sense("else", raw)]
    groups: list[list] = []
    for mark in marks:
        if groups and not raw[groups[-1][-1].end():mark.start()].strip(" &/\t"):
            groups[-1].append(mark)
        else:
            groups.append([mark])
    result = []
    prefix = raw[:marks[0].start()].strip()
    if prefix:
        if (prefix.startswith(("(", "（", "“", '"', "＝", "=")) or prefix == "*"
                or "＝" in prefix or "=" in prefix):
            issue(items, "definition_notes", prefix)
        else:
            result.append(Sense("else", prefix))
            issue(items, "unassigned_prefix", prefix)
    for index, group in enumerate(groups):
        end = groups[index + 1][0].start() if index + 1 < len(groups) else len(raw)
        translation = raw[group[-1].end():end].strip()
        codes = []
        for mark in group:
            code = POS_MAP.get(mark[1], "else")
            if code == "else":
                issue(items, "unknown_pos", mark[0])
            if mark[2] != ".":
                issue(items, "pos_punctuation", mark[0])
            codes.append(code)
        parts = translation.split("&")
        aligned = len(group) > 1 and len(parts) == len(group) and all(p.strip() for p in parts)
        if len(group) > 1 and not aligned:
            issue(items, "shared_translation", raw[group[0].start():end])
        for offset, code in enumerate(codes):
            value = parts[offset].strip() if aligned else translation
            if not value:
                issue(items, "empty_translation", group[offset][0])
            sense = Sense(code, value)
            if sense not in result:
                result.append(sense)
    return result


def source_warnings(text: str, phonetic: str, definition: str, items: list[dict]) -> None:
    # Observed source defects, not a dictionary or a correction database.
    if text.casefold() in {"television", "telephone"} and "telɪɡrɑ" in phonetic:
        issue(items, "known_source_issue", "源音标疑似与telegraph错配；原样保留。")
    if text.casefold() == "speak" and "spær" in phonetic:
        issue(items, "known_source_issue", "源音标疑似与sparrow错配；原样保留。")
    if text.casefold() == "woman" and "政治家" in definition:
        issue(items, "known_source_issue", "源释义疑似错配；原样保留。")


def convert_rows(rows: list[SourceRow]) -> tuple[list[WordEntry], dict]:
    entries: dict[str, WordEntry] = {}
    ranks: dict[str, int] = {}
    origins: dict[str, list[dict]] = {}
    records = []
    skipped = merged = 0
    for row in rows:
        items: list[dict] = []
        record = {"page": row.page, "number": row.number, "raw": row.raw,
                  "action": "import", "issues": items}
        records.append(record)
        if row.raw.startswith("#VALUE!"):
            record["action"] = "skip"
            issue(items, "damaged_head", row.raw)
            skipped += 1
            continue
        raw_head, raw_phonetic, definition = split_fields(row)
        text = normalize_head(raw_head, items)
        phonetic, rank = select_phonetic(raw_phonetic, items)
        senses = parse_senses(definition, items)
        source_warnings(text, raw_phonetic, definition, items)
        record.update(raw_head=raw_head, raw_phonetic=raw_phonetic,
                      raw_definition=definition, imported_text=text,
                      candidate_phonetic=phonetic, candidate_is_us=rank == 1,
                      senses=[{"pos": s.pos, "translation": s.translation} for s in senses])
        key = text.casefold()
        origins.setdefault(key, []).append({"page": row.page, "number": row.number})
        if key not in entries:
            entries[key] = WordEntry(text, senses, [], important=False, learned=False,
                                     phonetic=phonetic)
            ranks[key] = rank
            continue
        entry = entries[key]
        merged += 1
        record["action"] = "merge"
        issue(items, "duplicate_merged", entry.text)
        if text != entry.text:
            issue(items, "case_collision", f"{text} → {entry.text}")
        if phonetic != entry.phonetic:
            issue(items, "phonetic_conflict", f"{entry.phonetic} / {phonetic}")
        if rank > ranks[key]:
            entry.phonetic, ranks[key] = phonetic, rank
        for sense in senses:
            if sense not in entry.senses:
                entry.senses.append(sense)
        record["imported_text"] = entry.text
    if not entries:
        raise ValueError("没有可导入的词条；不写输出文件。")
    result = [clean_entry(entry) for entry in entries.values()]
    missing = []
    for left, right in zip(rows, rows[1:]):
        if right.number > left.number + 1:
            missing.extend(range(left.number + 1, right.number))
    counts = Counter(item["code"] for record in records for item in record["issues"])
    report = {
        "statistics": {
            "source_rows": len(rows), "skipped_rows": skipped,
            "merged_rows": merged, "imported_words": len(result),
            "imported_senses": sum(len(e.senses) for e in result),
            "words_with_phonetic": sum(bool(e.phonetic) for e in result),
            "rows_with_issues": sum(bool(r["issues"]) for r in records),
            "issue_counts": dict(sorted(counts.items())),
            "first_source_number": rows[0].number, "last_source_number": rows[-1].number,
            "missing_source_numbers": missing,
        },
        "notes": [
            "仅依据PDF原文，未调用OCR、联网词典或大模型接口。",
            "已知源错误仅报告未纠正；本报告不是完整的词典准确性审校。",
            "编号缺口是源数据缺口，不猜测补齐；导入数量不强行凑3500。",
            "默认非重点、待记；不生成关联词；不访问个人默认数据库。",
            "先选完整美式音标，否则首个完整原音标；美式片段不猜测展开。",
        ],
        "entries": [{"text": e.text, "phonetic": e.phonetic,
                     "phonetic_is_us": ranks[e.text.casefold()] == 1,
                     "source_rows": origins[e.text.casefold()]} for e in result],
        "records": records,
    }
    return result, report


def read_pdf(path: Path, first_page: int, last_page: int) -> tuple[list[str], int]:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtPdf import QPdfDocument

    application = QCoreApplication.instance() or QCoreApplication([])
    document = QPdfDocument()
    error = document.load(str(path))
    if error != QPdfDocument.Error.None_:
        raise ValueError(f"无法读取PDF：{error.name}")
    total = document.pageCount()
    if not 1 <= first_page <= last_page <= total:
        document.close()
        raise ValueError(f"页码范围必须在1–{total}内；当前{first_page}–{last_page}。")
    try:
        pages = [document.getAllText(index).text()
                 for index in range(first_page - 1, last_page)]
    finally:
        document.close()
    # Keep the Qt application alive until all PDF objects are finished.
    assert application is not None
    return pages, total


def validate_paths(source: Path, output: Path, raw: Path, report: Path) -> None:
    paths = [p.resolve() for p in (source, output, raw, report)]
    if len(set(paths)) != len(paths):
        raise ValueError("输入PDF、JSON、原始文本、报告必须使用不同路径。")
    protected = {p.resolve() for p in Path(__file__).parent.iterdir()
                 if p.is_file() and (p.suffix in {".py", ".md"} or p.name == "requirements.txt")}
    if any(p in protected for p in paths[1:]):
        raise ValueError("输出路径不能覆盖工具源文件或说明文件。")
    if not source.is_file():
        raise ValueError(f"输入PDF不存在：{source}")
    for path in paths[1:]:
        if path.exists() and not path.is_file():
            raise ValueError(f"输出路径不是文件：{path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="结构化原PDF（只读）")
    parser.add_argument("-o", "--output", required=True, type=Path, help="可导入的JSON输出路径")
    parser.add_argument("--raw", type=Path, help="默认OUTPUT.raw.txt")
    parser.add_argument("--report", type=Path, help="默认OUTPUT.report.json")
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int, default=116,
                        help="默认116，排除这份PDF末尾的分组表")
    args = parser.parse_args(argv)
    raw_path = args.raw or args.output.with_suffix(".raw.txt")
    report_path = args.report or args.output.with_suffix(".report.json")
    try:
        validate_paths(args.input, args.output, raw_path, report_path)
        pages, total_pages = read_pdf(args.input, args.first_page, args.last_page)
        entries, report = convert_rows(table_rows(pages, args.first_page))
        serialized = serialize_entries(entries)
        if parse_entries(serialized) != entries:
            raise ValueError("主程序导入格式往返校验失败。")
        with args.input.open("rb") as stream:
            source_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        report.update(source=str(args.input.resolve()),
                      source_sha256=source_digest,
                      format="kkMemorizeVocabulary", version=3,
                      tool="tools/pdf_vocabulary/extract.py")
        report["statistics"].update(total_pdf_pages=total_pages,
                                    first_page=args.first_page, last_page=args.last_page,
                                    excluded_pages=total_pages - len(pages))
        raw_text = "\n\n".join(f"===== PDF PAGE {number} =====\n{text}"
                                 for number, text in enumerate(pages, args.first_page)) + "\n"
        # All parsing/validation succeeds before touching the deliverables.
        # JSON is published last; individual replacements are atomic.
        for path in (raw_path, report_path, args.output):
            path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(raw_path, raw_text)
        write_text_atomically(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        write_text_atomically(args.output, serialized)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"转换失败：{error}\n")
    print(json.dumps(report["statistics"], ensure_ascii=False, indent=2))
    print(f"导入JSON：{args.output.resolve()}")
    print(f"原始文本：{raw_path.resolve()}")
    print(f"来源报告：{report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

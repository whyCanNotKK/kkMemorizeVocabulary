"""Versioned, offline JSON interchange for complete vocabulary entries."""
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile

from .domain import POS_OPTIONS, RelatedWord, Sense, ValidationError, WordEntry


FORMAT = "kkMemorizeVocabulary"
VERSION = 2


@dataclass
class ImportConflict:
    current: WordEntry
    incoming: WordEntry


@dataclass
class ImportResult:
    inserted: int = 0
    conflicts: list[ImportConflict] = field(default_factory=list)


def serialize_entries(entries: list[WordEntry]) -> str:
    words = []
    for entry in entries:
        word = asdict(entry)
        word.pop("id")
        if word["sort_order"] is None:
            word.pop("sort_order")
        words.append(word)
    return json.dumps({"format": FORMAT, "version": VERSION, "words": words},
                      ensure_ascii=False, indent=2) + "\n"


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValidationError(f"{label}：字段应为 {', '.join(fields)}。")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label}：必须是非空文本。")
    return value


def _array(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValidationError(f"{label}：必须是{'非空' if nonempty else ''}数组。")
    return value


def _senses(value, label):
    senses = []
    for number, item in enumerate(_array(value, label, nonempty=True), 1):
        location = f"{label} {number}"
        _object(item, ("pos", "translation"), location)
        pos = _text(item["pos"], location + " 词性")
        if pos not in {code for code, _ in POS_OPTIONS}:
            raise ValidationError(f"{location}：不支持的词性 {pos}。")
        senses.append(Sense(pos, _text(item["translation"], location + " 翻译")))
    return senses


def parse_entries(content: str) -> list[WordEntry]:
    try:
        document = json.loads(content)
    except (ValueError, RecursionError) as error:
        raise ValidationError("文件不是有效的 JSON。") from error
    _object(document, ("format", "version", "words"), "JSON 文件")
    if document["format"] != FORMAT:
        raise ValidationError("这不是 kkMemorizeVocabulary 的单词文件。")
    if type(document["version"]) is not int or document["version"] not in (1, VERSION):
        raise ValidationError("不支持的 JSON 格式版本，请使用 version: 1 或 2。")
    entries = []
    for number, word in enumerate(_array(document["words"], "words"), 1):
        label = f"主词 {number}"
        fields = ("text", "important", "learned", "senses", "related")
        _object(word, fields + (("sort_order",) if isinstance(word, dict) and "sort_order" in word else ()), label)
        order = word.get("sort_order")
        if "sort_order" in word and (type(order) is not int or order < 1):
            raise ValidationError(f"{label}：排序号必须是正整数。")
        for flag in ("important", "learned"):
            if type(word[flag]) is not bool:
                raise ValidationError(f"{label} {flag}：必须是 true 或 false。")
        related = []
        for index, child in enumerate(_array(word["related"], label + " 关联词"), 1):
            location = f"{label} 关联词 {index}"
            _object(child, ("text", "senses"), location)
            related.append(RelatedWord(_text(child["text"], location + " 名称"),
                                       _senses(child["senses"], location + " 释义")))
        entries.append(WordEntry(_text(word["text"], label + " 名称"),
                                 _senses(word["senses"], label + " 释义"), related,
                                 word["important"], word["learned"], sort_order=order))
    return entries


def read_entries(path: str | Path) -> list[WordEntry]:
    # Accept the optional UTF-8 BOM produced by some Windows editors.
    return parse_entries(Path(path).read_text(encoding="utf-8-sig"))


def write_entries(path: str | Path, entries: list[WordEntry]):
    """Replace the destination only after a complete file has been written."""
    path = Path(path)
    content = serialize_entries(entries)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

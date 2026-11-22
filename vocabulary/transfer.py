"""Versioned, offline JSON interchange for complete vocabulary entries."""
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile

from .domain import POS_OPTIONS, RelatedWord, Sense, ValidationError, WordEntry, normalize_pos
from .import_merge import AutoMergeStats


FORMAT = "kkMemorizeVocabulary"
VERSION = 3


@dataclass
class ImportConflict:
    current: WordEntry
    incoming: WordEntry
    source_index: int | None = None


@dataclass
class ImportResult:
    inserted: int = 0
    conflicts: list[ImportConflict] = field(default_factory=list)
    auto_merge_stats: AutoMergeStats | None = None


def _import_flag_locations(content):
    """Index booleans once using JSON decoding, including escaped keys and nested values."""
    decoder = json.JSONDecoder()

    def skip_space(position):
        while content[position] in ' \t\r\n':
            position += 1
        return position

    def members(position):
        position = skip_space(position + 1)  # Validated object opening brace.
        while content[position] != '}':
            key, position = decoder.raw_decode(content, position)
            position = skip_space(position)
            start = skip_space(position + 1)  # Colon.
            _, end = decoder.raw_decode(content, start)
            yield key, start, end
            position = skip_space(end)
            if content[position] == ',':
                position = skip_space(position + 1)

    words_start = None
    for key, start, _ in members(skip_space(0)):
        if key == 'words':
            words_start = start
    # Like json.loads, only the final occurrence counts if a word file repeats a key.
    if words_start is None:
        raise ValidationError('导入文件缺少words。')
    locations = {}
    position, index = skip_space(words_start + 1), 0
    while content[position] != ']':
        for flag, begin, end in members(position):
            if flag in {'important', 'learned'}:
                locations[index, flag] = begin, end
        _, position = decoder.raw_decode(content, position)
        position = skip_space(position)
        if content[position] == ',':
            position = skip_space(position + 1)
        index += 1
    return locations


class ImportSource:
    """Keep a validated import snapshot and update only the selected record's flag."""
    def __init__(self, path, content):
        self.path = Path(path)
        self.content = content
        self._template = content
        self._flag_locations = _import_flag_locations(content)
        self._replacements = {}

    @classmethod
    def read(cls, path, parser):
        content = Path(path).read_text(encoding="utf-8-sig")
        parsed = parser(content)
        return cls(path, content), parsed

    def set_flag(self, source_index, flag, value):
        if flag not in {"important", "learned"} or type(value) is not bool:
            raise ValueError("不支持的状态修改。")
        if self.path.read_text(encoding="utf-8-sig") != self.content:
            raise ValueError("导入JSON文件已被外部修改，请重新导入后再修改状态。")
        location = self._flag_locations.get((source_index, flag))
        if type(source_index) is not int or location is None:
            raise ValueError("找不到导入文件中的对应词条，请重新导入。")
        replacements = self._replacements.copy()
        literal = 'true' if value else 'false'
        if literal == self._template[location[0]:location[1]]:
            replacements.pop(location, None)
        else:
            replacements[location] = literal
        parts, cursor = [], 0
        for (start, end), token in sorted(replacements.items()):
            parts.extend((self._template[cursor:start], token))
            cursor = end
        parts.append(self._template[cursor:])
        content = ''.join(parts)
        write_text_atomically(self.path, content)
        self.content = content
        self._replacements = replacements


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


def _text(value, label, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        requirement = "文本（允许为空）" if allow_empty else "非空文本"
        raise ValidationError(f"{label}：必须是{requirement}。")
    return value


def _array(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValidationError(f"{label}：必须是{'非空' if nonempty else ''}数组。")
    return value


def _phonetic(value, label):
    if not isinstance(value, str):
        raise ValidationError(f"{label}：音标必须是文本（允许为空）。")
    return value


def _senses(value, label):
    senses = []
    for number, item in enumerate(_array(value, label, nonempty=True), 1):
        location = f"{label} {number}"
        _object(item, ("pos", "translation"), location)
        pos = normalize_pos(_text(item["pos"], location + " 词性"))
        if pos not in {code for code, _ in POS_OPTIONS}:
            raise ValidationError(f"{location}：不支持的词性 {pos}。")
        senses.append(Sense(pos, _text(item["translation"], location + " 翻译", allow_empty=True)))
    return senses


def parse_entries(content: str) -> list[WordEntry]:
    try:
        document = json.loads(content)
    except (ValueError, RecursionError) as error:
        raise ValidationError("文件不是有效的 JSON。") from error
    _object(document, ("format", "version", "words"), "JSON 文件")
    if document["format"] != FORMAT:
        raise ValidationError("这不是 kkMemorizeVocabulary 的单词文件。")
    if type(document["version"]) is not int or document["version"] != VERSION:
        raise ValidationError(f"不支持的 JSON 格式版本，请使用 version: {VERSION}。")
    entries = []
    for number, word in enumerate(_array(document["words"], "words"), 1):
        label = f"主词 {number}"
        fields = ("text", "phonetic", "important", "learned", "senses", "related")
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
            _object(child, ("text", "phonetic", "senses"), location)
            related.append(RelatedWord(_text(child["text"], location + " 名称"),
                                       _senses(child["senses"], location + " 释义"),
                                       _phonetic(child["phonetic"], location)))
        entries.append(WordEntry(_text(word["text"], label + " 名称"),
                                 _senses(word["senses"], label + " 释义"), related,
                                 word["important"], word["learned"], sort_order=order,
                                 phonetic=_phonetic(word["phonetic"], label)))
    return entries


def read_entries(path: str | Path) -> list[WordEntry]:
    # Accept the optional UTF-8 BOM produced by some Windows editors.
    return parse_entries(Path(path).read_text(encoding="utf-8-sig"))


def write_entries(path: str | Path, entries: list[WordEntry]):
    """Replace the destination only after a complete file has been written."""
    write_text_atomically(path, serialize_entries(entries))


def write_text_atomically(path: str | Path, content: str):
    path = Path(path)
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

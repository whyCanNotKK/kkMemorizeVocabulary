from dataclasses import dataclass, field
import sys


POS_OPTIONS = (
    ("n.", "名词"), ("v.", "动词"), ("adj.", "形容词"),
    ("adv.", "副词"), ("prep.", "介词"), ("pron.", "代词"),
    ("conj.", "连词"), ("interj.", "感叹词"), ("else", "其他"),
)
_SHORTCUT_MODIFIER = "Meta" if sys.platform == "darwin" else "Ctrl"
DEFAULT_SHORTCUTS = {
    "add_related": f"{_SHORTCUT_MODIFIER}+N", "remove_related": f"{_SHORTCUT_MODIFIER}+M",
    "add_sense": f"{_SHORTCUT_MODIFIER}+J", "remove_sense": f"{_SHORTCUT_MODIFIER}+K",
    "save": f"{_SHORTCUT_MODIFIER}+Return", "important": f"{_SHORTCUT_MODIFIER}+I",
    "toggle_visibility": f"{_SHORTCUT_MODIFIER}+S", "edit_selected": f"{_SHORTCUT_MODIFIER}+E",
}
SHORTCUT_LABELS = {
    "add_related": "新增关联词", "remove_related": "删除关联词",
    "add_sense": "新增释义", "remove_sense": "删除释义",
    "save": "保存单词", "important": "单词 重点/非重点",
    "toggle_visibility": "显示/隐藏 所有释义",
    "edit_selected": "编辑选定的单词",
}


def shortcut_candidates(default):
    yield default
    yield f"{_SHORTCUT_MODIFIER}+Shift+{default.split('+')[-1]}"
    yield from (f"{_SHORTCUT_MODIFIER}+Alt+Shift+F{number}" for number in range(1, 25))


CLEANUP_COMMANDS = {
    "delete study record": "删除学习记录。",
    "clean vocabulary only": "删除学习记录不包含的词汇。",
    "remove all": "删除所有学习记录和所有词汇。",
}


@dataclass
class Sense:
    pos: str = ""
    translation: str = ""


@dataclass
class RelatedWord:
    text: str = ""
    senses: list[Sense] = field(default_factory=lambda: [Sense()])
    phonetic: str = ""


@dataclass
class WordEntry:
    text: str = ""
    senses: list[Sense] = field(default_factory=lambda: [Sense()])
    related: list[RelatedWord] = field(default_factory=list)
    important: bool = True
    learned: bool = False
    id: int | None = None
    sort_order: int | None = None
    phonetic: str = ""


def same_entry_content(left: WordEntry, right: WordEntry) -> bool:
    """Compare cleaned entry content, including ordered senses and related words."""
    return (left.text, left.phonetic, left.senses, left.related, left.important, left.learned) == (
        right.text, right.phonetic, right.senses, right.related, right.important, right.learned)


class ValidationError(ValueError):
    pass


class DuplicateWord(ValidationError):
    def __init__(self, entry_id: int):
        self.entry_id = entry_id
        super().__init__("这个主词已经存在，当前输入已保留。")


class WordContentValidationError(ValidationError):
    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


def normalize_pos(pos: str) -> str:
    """Keep the previous Chinese POS code readable in drafts and JSON backups."""
    return 'else' if pos == '其他' else pos


def clean_senses(senses: list[Sense], label: str) -> list[Sense]:
    result = []
    for sense in senses:
        pos, translation = normalize_pos(sense.pos.strip()), sense.translation.strip()
        if not pos and not translation:
            continue
        if pos not in {item[0] for item in POS_OPTIONS}:
            raise ValidationError(f"{label}：请选择词性，翻译可留空。")
        result.append(Sense(pos, translation))
    if not result:
        raise ValidationError(f"{label}：至少需要一条已选择词性的释义，翻译可留空。")
    return result


def clean_word_content(word: RelatedWord) -> RelatedWord:
    text = word.text.strip()
    if not text:
        raise WordContentValidationError("单词拼写", "请填写单词。")
    try:
        senses = clean_senses(word.senses, text)
    except ValidationError as error:
        raise WordContentValidationError("释义词性", str(error)) from error
    return RelatedWord(text, senses, word.phonetic.strip())


def clean_entry(entry: WordEntry) -> WordEntry:
    if entry.sort_order is not None and (type(entry.sort_order) is not int or entry.sort_order < 1):
        raise ValidationError("排序号必须是正整数。")
    text = entry.text.strip()
    if not text:
        raise ValidationError("请先输入主词。")
    related = []
    for index, item in enumerate(entry.related, 1):
        if not item.text.strip() and not item.phonetic.strip() and not any(s.pos or s.translation.strip() for s in item.senses):
            continue
        if not item.text.strip():
            raise ValidationError(f"关联词 {index}：请填写单词。")
        related.append(RelatedWord(item.text.strip(), clean_senses(item.senses, f"关联词 {index}"),
                                   item.phonetic.strip()))
    return WordEntry(text, clean_senses(entry.senses, "主词"), related,
                     entry.important, entry.learned, entry.id, entry.sort_order, entry.phonetic.strip())


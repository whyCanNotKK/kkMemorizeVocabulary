from dataclasses import dataclass, field


POS_OPTIONS = (
    ("n.", "名词"), ("v.", "动词"), ("adj.", "形容词"),
    ("adv.", "副词"), ("prep.", "介词"), ("pron.", "代词"),
    ("conj.", "连词"), ("interj.", "感叹词"), ("其他", "其他"),
)
DEFAULT_SHORTCUTS = {
    "add_related": "Ctrl+N", "remove_related": "Ctrl+M",
    "add_sense": "Ctrl+J", "remove_sense": "Ctrl+K",
    "save": "Ctrl+Return", "important": "Ctrl+I",
}
SHORTCUT_LABELS = {
    "add_related": "新增关联词", "remove_related": "移除最后一个关联词",
    "add_sense": "新增当前词的释义", "remove_sense": "移除当前词的末项释义",
    "save": "保存词条", "important": "切换录入主词的重点状态",
}


@dataclass
class Sense:
    pos: str = ""
    translation: str = ""


@dataclass
class RelatedWord:
    text: str = ""
    senses: list[Sense] = field(default_factory=lambda: [Sense()])


@dataclass
class WordEntry:
    text: str = ""
    senses: list[Sense] = field(default_factory=lambda: [Sense()])
    related: list[RelatedWord] = field(default_factory=list)
    important: bool = True
    learned: bool = False
    id: int | None = None


class ValidationError(ValueError):
    pass


class DuplicateWord(ValidationError):
    def __init__(self, entry_id: int):
        self.entry_id = entry_id
        super().__init__("这个主词已经存在，当前输入已保留。")


def clean_senses(senses: list[Sense], label: str) -> list[Sense]:
    result = []
    for sense in senses:
        pos, translation = sense.pos.strip(), sense.translation.strip()
        if not pos and not translation:
            continue
        if pos not in {item[0] for item in POS_OPTIONS} or not translation:
            raise ValidationError(f"{label}：请补齐词性和翻译。")
        result.append(Sense(pos, translation))
    if not result:
        raise ValidationError(f"{label}：至少需要一条完整释义。")
    return result


def clean_entry(entry: WordEntry) -> WordEntry:
    text = entry.text.strip()
    if not text:
        raise ValidationError("请先输入主词。")
    related = []
    for index, item in enumerate(entry.related, 1):
        if not item.text.strip() and not any(s.pos or s.translation.strip() for s in item.senses):
            continue
        if not item.text.strip():
            raise ValidationError(f"关联词 {index}：请填写单词。")
        related.append(RelatedWord(item.text.strip(), clean_senses(item.senses, f"关联词 {index}")))
    return WordEntry(text, clean_senses(entry.senses, "主词"), related,
                     entry.important, entry.learned, entry.id)


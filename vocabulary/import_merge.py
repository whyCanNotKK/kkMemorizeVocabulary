"""Conservative, local-first merge decisions with no persistence or Qt dependencies."""
from copy import deepcopy
from dataclasses import dataclass, field

from .domain import WordEntry


AUTO_MERGE_TOOLTIP = "\n".join((
    '一、不自动合并的标准：',
    '    1、同词同词性的译文，导入长度 > 本地长度（本地空译文除外；含关联词）。',
    '    2、本地或导入方任一单词自身出现重复词性。',
    '    3、任一方存在重名关联词（忽略大小写及首尾空白）。',
    '二、合并逻辑：',
    '    1、以本地为主；任一项不通过，整组跳过，交人工判断。',
    '    2、关联词：额外的追加，同名的继续比较，匹配规则同上。',
    '    3、音标（含关联词）：仅本地为空时补入。',
    '    4、译文（含关联词）：空译文补齐，新词性追加；同词性较短或等长保留本地。',
    '    5、长度：去首尾空白后计数，内部空格、标点和换行均计入。',
    '    6、收藏、已记下及原有排序：保留本地；合并后自动保存。',
))


@dataclass
class AutoMergeStats:
    changed: int = 0
    unchanged: int = 0


@dataclass
class MergeDecision:
    entry: WordEntry | None = None
    reason: str = ''


@dataclass
class AutoMergeResult:
    processed_indices: list[int] = field(default_factory=list)
    entries: dict[str, WordEntry] = field(default_factory=dict)
    skipped: dict[int, str] = field(default_factory=dict)
    stats: AutoMergeStats = field(default_factory=AutoMergeStats)


def related_key(word):
    return word.text.strip().casefold()


def _merge_word(local, incoming):
    """Modify only the working copy; return a reason if any definition needs review."""
    by_pos = {sense.pos: sense for sense in local.senses}
    for sense in incoming.senses:
        current = by_pos.get(sense.pos)
        if current is None:
            local.senses.append(deepcopy(sense))
        elif not current.translation.strip():
            current.translation = sense.translation.strip()
        elif len(sense.translation.strip()) > len(current.translation.strip()):
            return f'{local.text}：{sense.pos} 导入译文更长'
    if not local.phonetic.strip():
        local.phonetic = incoming.phonetic
    return ''


def plan_auto_merge(current: WordEntry, incoming: WordEntry) -> MergeDecision:
    """Check every structural ambiguity before merging an independent working copy."""
    for entry in (current, incoming):
        for word in (entry, *entry.related):
            if len({sense.pos for sense in word.senses}) != len(word.senses):
                return MergeDecision(reason=f'{word.text}：存在重复词性')
        if len({related_key(word) for word in entry.related}) != len(entry.related):
            return MergeDecision(reason=f'{entry.text}：存在重名关联词')

    merged = deepcopy(current)
    reason = _merge_word(merged, incoming)
    if reason:
        return MergeDecision(reason=reason)
    by_name = {related_key(word): word for word in merged.related}
    for word in incoming.related:
        local = by_name.get(related_key(word))
        if local is None:
            merged.related.append(deepcopy(word))
        else:
            reason = _merge_word(local, word)
            if reason:
                return MergeDecision(reason=reason)
    return MergeDecision(entry=merged)

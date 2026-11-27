"""Explicit, non-recursive relationship changes for interactive saves."""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field

from .domain import RelatedWord, WordEntry, WordContentValidationError, clean_word_content


def name(text):
    return text.strip().casefold()


def content(word):
    cleaned = clean_word_content(RelatedWord(word.text, word.senses, word.phonetic))
    return cleaned.phonetic, cleaned.senses


def snapshot(entry):
    """Flags and sorting may change independently without invalidating content."""
    return entry.text, entry.phonetic, entry.senses, entry.related


def validate_names(entry, previous=None):
    counts = Counter(name(word.text) for word in entry.related)
    old = Counter(name(word.text) for word in previous.related) if previous else Counter()
    if any(count > 1 and count > old[key] for key, count in counts.items()):
        raise WordContentValidationError('关联词', '不可有重复同名关联词')
    own = name(entry.text)
    old_self = old[own] if previous and name(previous.text) == own else 0
    if counts[own] > old_self:
        raise WordContentValidationError('关联词', '关联词不可与主词同名')


def changes(baseline, draft):
    old = {name(word.text) for word in baseline.related} if baseline else set()
    new = {name(word.text) for word in draft.related}
    return new - old, old - new


@dataclass
class LinkConflict:
    target_id: int
    related_index: int


@dataclass
class LinkPlan:
    draft: WordEntry
    before: WordEntry | None
    added: set[str]
    removed: set[str]
    targets: dict[str, WordEntry]
    conflicts: list[LinkConflict] = field(default_factory=list)

    def drafts(self):
        return deepcopy(self.draft), {word.id: deepcopy(word) for word in self.targets.values()}


@dataclass
class LinkSaveResult:
    saved: WordEntry
    changes: list[tuple[WordEntry | None, WordEntry]]

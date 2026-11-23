"""Committed vocabulary changes and cache policy, independent of Qt and SQL.

Writers describe facts, never cache validity. Only this module decides which
snapshots can still answer a query after a successful commit.
"""
from dataclasses import dataclass, field

from .domain import WordEntry
from .list_state import CATEGORIES


@dataclass(frozen=True)
class LocationMap:
    owners: dict[int, int | None] = field(default_factory=dict)
    senses: dict[int, dict[int, int | None]] = field(default_factory=dict)
    replaced: frozenset[int] = frozenset()
    reset: bool = False

    def target(self, part, *, editing=False):
        if part is None:
            return None
        key, owner, sense, column = part
        new_owner = self.owners.get(owner, owner)
        new_sense = self.senses.get(owner, {}).get(sense, sense)
        word_name = column == 1 and sense == 0
        lost = (self.reset or new_owner is None or
                (not word_name and (owner in self.replaced or new_sense is None)))
        if lost:
            return None if editing else (key, -1, 0, 1)
        return key, new_owner, (0 if word_name else new_sense), column


@dataclass(frozen=True)
class CommittedChange:
    before: WordEntry | None = None
    after: WordEntry | None = None
    locations: LocationMap = field(default_factory=LocationMap)
    sort_orders: dict[int, int] = field(default_factory=dict)
    # A proved page-local move supplies its complete new ID order.
    page_order: tuple[str, tuple[int, ...]] | None = None
    bulk: bool = False

    @property
    def entry_id(self):
        entry = self.after or self.before
        return entry.id if entry is not None else None

    def target(self, part, *, editing=False):
        if part is None:
            return None
        if self.bulk:
            return None if editing else (part[0], -1, 0, 1)
        if part[0] != self.entry_id:
            return part
        if self.after is None:
            return None
        return self.locations.target(part, editing=editing)

    def view(self, state):
        part, selected, scroll = state
        part = self.target(part)
        return part, selected and part is not None, scroll


@dataclass(frozen=True)
class ViewIntent:
    kind: str = 'restore'
    anchor: tuple | None = None
    reveal_id: int | None = None

    def action(self):
        return dict(kind=self.kind, anchor=self.anchor, reveal_id=self.reveal_id)


@dataclass(frozen=True)
class SyncPlan:
    change: CommittedChange
    invalidate_study: bool = False


def _search_content(entry):
    # Order inside a word does not influence matching or ranking. Preserve
    # multiplicity, but ignore case, phonetics and parts of speech.
    return (entry.text.casefold(),
            sorted(word.text.casefold() for word in entry.related),
            sorted(s.translation.casefold() for s in entry.senses),
            sorted(s.translation.casefold() for w in entry.related for s in w.senses))


def synchronize(state, change):
    """Apply one committed change to bounded snapshots and return study work.

    An invalid cache is never made valid by a patch. Counts on unrelated
    categories are invalidated too when membership changes, since each page
    carries the three tab totals.
    """
    before, after = change.before, change.after
    for cache in state._caches():
        cache.view_state = change.view(cache.view_state)
    for position in state.search_positions.values():
        position.view_state = change.view(position.view_state)
    if change.bulk:
        state.invalidate()
        return SyncPlan(change, invalidate_study=True)
    if change.sort_orders:
        state.patch_sort_orders(change.sort_orders)
        keep = change.page_order[0] if change.page_order else None
        state.invalidate([key for key in CATEGORIES if key != keep])
        if change.page_order:
            category, ids = change.page_order
            cache = state.normal[category]
            entries = {entry.id: entry for entry in cache.entries}
            if set(ids) == entries.keys():
                cache.entries = [entries[key] for key in ids]
            else:
                cache.valid = False
        return SyncPlan(change)
    if before is None and after is None:
        return SyncPlan(change)
    membership = before is None or after is None or before.learned != after.learned
    if after is None:
        state.remove_entry(before.id)
    else:
        state.patch_entry(after)
    if membership:
        state.invalidate()
    elif _search_content(before) != _search_content(after):
        state.invalidate([], search=True)
    return SyncPlan(change, invalidate_study=after is None)

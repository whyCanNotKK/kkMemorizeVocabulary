"""Run-local word-page snapshots, independent of widgets and SQLite connections."""
from copy import deepcopy
from dataclasses import dataclass, field, replace

from .domain import WordEntry
from .list_query import PAGE_SIZE, normalize_keyword


CATEGORIES = ("pending", "all", "learned")
EMPTY_VIEW = (None, False, 0)


@dataclass
class PagePosition:
    keyword: str = ""
    page_number: int | None = 1
    view_state: tuple = EMPTY_VIEW
    scroll: int = 0


@dataclass
class PageCache:
    category: str
    keyword: str = ""
    page_number: int | None = 1
    total: int = 0
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(CATEGORIES, 0))
    entries: list[WordEntry] = field(default_factory=list)
    revealed_ids: set[int] = field(default_factory=set)
    view_state: tuple = EMPTY_VIEW
    scroll: int = 0
    valid: bool = False
    before_id: int | None = None
    after_id: int | None = None

    @property
    def page_count(self):
        return (self.total + PAGE_SIZE - 1) // PAGE_SIZE

    @property
    def ids(self):
        return {entry.id for entry in self.entries}

    def position(self):
        return PagePosition(self.keyword, self.page_number, self.view_state, self.scroll)


class ListState:
    """Three normal snapshots and a single shared search snapshot, never page history."""

    def __init__(self):
        self.normal = {category: PageCache(category) for category in CATEGORIES}
        self.search = None
        self.search_positions = {category: PagePosition() for category in CATEGORIES}
        self.eye_visible = dict.fromkeys((*CATEGORIES, "study17"), False)
        self.revision = 0

    def cache_for(self, category, keyword="", page_number=None):
        keyword = normalize_keyword(keyword)
        cache = self.search if keyword else self.normal[category]
        if (cache is None or not cache.valid or cache.category != category
                or cache.keyword != keyword):
            return None
        if page_number is not None and cache.page_number != page_number:
            # An empty snapshot has no displayed page, but remains reusable for page 1.
            if cache.total or page_number != 1:
                return None
        return cache

    def apply(self, result):
        """Commit a successful result; callers reject stale request/revision pairs first."""
        keyword = normalize_keyword(result.keyword)
        previous = self.search if keyword else self.normal[result.category]
        same_page = (previous is not None and previous.category == result.category
                     and previous.keyword == keyword and previous.page_number == result.page_number)
        position = self.search_positions[result.category] if keyword else None
        restore_position = (position is not None and position.keyword == keyword
                            and position.page_number == result.page_number)
        entries = deepcopy(list(result.entries))
        ids = {entry.id for entry in entries}
        if keyword:
            revealed = ids
        elif same_page:
            revealed = previous.revealed_ids & ids
            if self.eye_visible[result.category]:
                revealed |= ids - previous.ids
        else:
            revealed = ids if self.eye_visible[result.category] else set()
        cache = PageCache(
            category=result.category, keyword=keyword, page_number=result.page_number,
            total=result.total, counts=dict(result.counts), entries=entries,
            revealed_ids=revealed, valid=True,
            before_id=result.before_id, after_id=result.after_id,
            view_state=(previous.view_state if same_page else
                        position.view_state if restore_position else EMPTY_VIEW),
            scroll=(previous.scroll if same_page else
                    position.scroll if restore_position else 0),
        )
        if keyword:
            self.search = cache
            self.search_positions[result.category] = cache.position()
        else:
            self.normal[result.category] = cache
        return cache

    def remember_search_position(self):
        if self.search is not None:
            self.search_positions[self.search.category] = self.search.position()

    def reset_search_positions(self, keyword, *, except_category=None):
        keyword = normalize_keyword(keyword)
        for category in CATEGORIES:
            if category != except_category:
                self.search_positions[category] = PagePosition(keyword=keyword)

    def invalidate(self, categories=None, *, search=True):
        for category in CATEGORIES if categories is None else categories:
            self.normal[category].valid = False
        if search and self.search is not None:
            self.search.valid = False

    def bump_revision(self):
        self.revision += 1
        return self.revision

    def _caches(self):
        yield from self.normal.values()
        if self.search is not None:
            yield self.search

    def patch_entry(self, entry):
        """Replace resident content only; membership/ranking invalidation belongs to list_sync."""
        patched = set()
        for cache in self._caches():
            for index, cached in enumerate(cache.entries):
                if cached.id == entry.id:
                    cache.entries[index] = deepcopy(entry)
                    patched.add(cache.category)
        return patched

    def patch_sort_orders(self, updates):
        for cache in self._caches():
            cache.entries = [replace(entry, sort_order=updates[entry.id])
                             if entry.id in updates else entry for entry in cache.entries]

    def remove_entry(self, entry_id):
        """Drop resident copies after deletion; a page query supplies any replacement."""
        for cache in self._caches():
            if entry_id in cache.ids:
                cache.entries = [entry for entry in cache.entries if entry.id != entry_id]
                cache.revealed_ids.discard(entry_id)
                cache.valid = False

    def set_revealed(self, category, entry_id, expanded, *, manual=True):
        cache = self.normal[category]
        if entry_id not in cache.ids:
            return self.eye_visible[category]
        if expanded:
            cache.revealed_ids.add(entry_id)
        else:
            cache.revealed_ids.discard(entry_id)
        if manual:
            self.update_eye_from_visible(category, cache.ids, cache.revealed_ids)
        return self.eye_visible[category]

    def update_eye_from_visible(self, category, ids, revealed_ids):
        ids = set(ids)
        if ids and ids <= revealed_ids:
            self.eye_visible[category] = True
        elif ids and not ids.intersection(revealed_ids):
            self.eye_visible[category] = False
        return self.eye_visible[category]

    def toggle_visibility(self, category):
        visible = not self.eye_visible[category]
        self.eye_visible[category] = visible
        if category in self.normal:
            cache = self.normal[category]
            cache.revealed_ids.clear()
            if visible:
                cache.revealed_ids.update(cache.ids)
        return visible

"""Bounded vocabulary reads and exact search ranking, independent of Qt."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3

from .domain import RelatedWord, Sense, WordEntry


PAGE_SIZE = 500
_CATEGORIES = {"pending": "learned=0", "all": "1", "learned": "learned=1"}


def normalize_keyword(text):
    return text.strip().casefold()


@dataclass
class PageResult:
    category: str
    keyword: str
    page_number: int | None
    total: int
    counts: dict[str, int]
    entries: list[WordEntry]
    anchor_id: int | None = None
    before_id: int | None = None
    after_id: int | None = None
    _page_size: int = field(default=PAGE_SIZE, repr=False, compare=False, kw_only=True)

    @property
    def page_count(self):
        return (self.total + self._page_size - 1) // self._page_size


@contextmanager
def read_snapshot(connection):
    """Do not commit or roll back a transaction owned by a caller."""
    owned = not connection.in_transaction
    if owned:
        connection.execute("BEGIN")
    try:
        yield
    finally:
        if owned:
            connection.rollback()


def get_entries_by_ids(connection, ids):
    """Load only the requested aggregates, in four queries and caller order."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    payload = json.dumps(ids)
    selected = "SELECT CAST(value AS INTEGER) FROM json_each(?)"
    with read_snapshot(connection):
        entries = {
            row["id"]: WordEntry(row["text"], [], [], bool(row["important"]),
                                  bool(row["learned"]), row["id"], row["sort_order"], row["phonetic"])
            for row in connection.execute(f"SELECT * FROM words WHERE id IN ({selected})", (payload,))
        }
        children = {}
        for row in connection.execute(
                f"SELECT * FROM related_words WHERE word_id IN ({selected}) ORDER BY position,id", (payload,)):
            child = RelatedWord(row["text"], [], row["phonetic"])
            children[row["id"]] = child
            entries[row["word_id"]].related.append(child)
        for row in connection.execute(
                f"SELECT word_id,pos,translation FROM senses WHERE word_id IN ({selected}) ORDER BY position,id",
                (payload,)):
            entries[row["word_id"]].senses.append(Sense(row["pos"], row["translation"]))
        for row in connection.execute(f"""SELECT related_id,pos,translation FROM senses
                WHERE related_id IN (SELECT id FROM related_words WHERE word_id IN ({selected}))
                ORDER BY position,id""", (payload,)):
            children[row["related_id"]].senses.append(Sense(row["pos"], row["translation"]))
    return [entries[entry_id] for entry_id in ids if entry_id in entries]


def _counts(connection):
    values = dict(connection.execute("SELECT learned,COUNT(*) FROM words GROUP BY learned"))
    pending, learned = values.get(0, 0), values.get(1, 0)
    return {"pending": pending, "all": pending + learned, "learned": learned}


def _ranked_search(category):
    # Each text stays separate; neither a cross-field match nor an earlier
    # position from a lower-priority field can win. SQL positions are 1-based.
    return f"""WITH base AS MATERIALIZED (
        SELECT id,text,learned,sort_order FROM words WHERE {_CATEGORIES[category]}
    ), candidates AS MATERIALIZED (
        SELECT id AS word_id,0 AS priority,instr(kk_casefold(text),:keyword)-1 AS position FROM base
        UNION ALL SELECT r.word_id,1,instr(kk_casefold(r.text),:keyword)-1
            FROM related_words r JOIN base b ON b.id=r.word_id
        UNION ALL SELECT s.word_id,2,instr(kk_casefold(s.translation),:keyword)-1
            FROM senses s JOIN base b ON b.id=s.word_id
        UNION ALL SELECT r.word_id,3,instr(kk_casefold(s.translation),:keyword)-1
            FROM senses s JOIN related_words r ON r.id=s.related_id JOIN base b ON b.id=r.word_id
    ), hits AS (
        SELECT word_id,priority,position,
            ROW_NUMBER() OVER (PARTITION BY word_id ORDER BY priority,position) AS match_number
        FROM candidates WHERE position>=0
    ), ordered AS MATERIALIZED (
        SELECT b.id,ROW_NUMBER() OVER (
            ORDER BY h.priority,h.position,b.learned,b.sort_order,b.id) AS ordinal
        FROM hits h JOIN base b ON b.id=h.word_id WHERE h.match_number=1
    )"""


def _search_page_ids(connection, category, keyword, page_number, page_size, anchor_id):
    rows = connection.execute(_ranked_search(category) + """, info AS (
        SELECT COUNT(*) AS total,MAX(CASE WHEN id=:anchor THEN ordinal END) AS anchor_ordinal FROM ordered
    ), resolved AS (
        SELECT total,anchor_ordinal,
            CASE WHEN total=0 THEN NULL
                 WHEN anchor_ordinal IS NOT NULL THEN (anchor_ordinal-1)/:size+1
                 ELSE MIN(MAX(:page,1),(total+:size-1)/:size) END AS page_number
        FROM info
    ) SELECT r.total,r.anchor_ordinal,r.page_number,o.id,o.ordinal FROM resolved r
      LEFT JOIN ordered o ON o.ordinal BETWEEN (r.page_number-1)*:size AND r.page_number*:size+1
      ORDER BY o.ordinal""", {
        "keyword": keyword, "anchor": anchor_id, "size": page_size, "page": page_number,
    }).fetchall()
    first = rows[0]
    number, total = first["page_number"], first["total"]
    offset = (number - 1) * page_size if number is not None else 0
    ids, before, after = [], None, None
    for row in rows:
        ordinal = row["ordinal"]
        if ordinal is None:
            continue
        if ordinal == offset:
            before = row["id"]
        elif ordinal == offset + page_size + 1:
            after = row["id"]
        else:
            ids.append(row["id"])
    return ids, number, total, anchor_id if first["anchor_ordinal"] is not None else None, before, after


def _ordinary_page_ids(connection, category, counts, page_number, page_size, anchor_id):
    total = counts[category]
    if not total:
        return [], None, 0, None, None, None
    where = _CATEGORIES[category]
    anchor = connection.execute(
        f"SELECT learned,sort_order,id FROM words WHERE {where} AND id=?", (anchor_id,)).fetchone()
    if anchor is not None:
        position = connection.execute(f"""SELECT COUNT(*) FROM words
            WHERE {where} AND (learned,sort_order,id)<(?,?,?)""", tuple(anchor)).fetchone()[0]
        page_number = position // page_size + 1
    else:
        anchor_id = None
        page_number = min(max(page_number, 1), (total + page_size - 1) // page_size)
    offset = (page_number - 1) * page_size
    start = max(0, offset - 1)
    rows = connection.execute(f"""SELECT id FROM words WHERE {where}
        ORDER BY learned,sort_order,id LIMIT ? OFFSET ?""",
        (page_size + 1 + bool(offset), start)).fetchall()
    before = rows.pop(0)["id"] if offset else None
    after = rows.pop()["id"] if len(rows) > page_size else None
    return [row["id"] for row in rows], page_number, total, anchor_id, before, after


def query_page(connection, category, keyword="", page_number=1, anchor_id=None, page_size=PAGE_SIZE):
    if category not in _CATEGORIES:
        raise ValueError("无效的单词分类。")
    if type(page_size) is not int or page_size < 1 or type(page_number) is not int:
        raise ValueError("分页大小和页码必须是整数，分页大小必须大于0。")
    keyword = normalize_keyword(keyword)
    connection.create_function("kk_casefold", 1, str.casefold, deterministic=True)
    with read_snapshot(connection):
        counts = _counts(connection)
        if keyword:
            values = _search_page_ids(connection, category, keyword, page_number, page_size, anchor_id)
        else:
            values = _ordinary_page_ids(connection, category, counts, page_number, page_size, anchor_id)
        ids, number, total, anchor, before, after = values
        entries = get_entries_by_ids(connection, ids)
    return PageResult(category, keyword, number, total, counts, entries, anchor, before, after,
                      _page_size=page_size)


def export_entries(connection, category, keyword=""):
    if category not in _CATEGORIES:
        raise ValueError("无效的单词分类。")
    keyword = normalize_keyword(keyword)
    connection.create_function("kk_casefold", 1, str.casefold, deterministic=True)
    with read_snapshot(connection):
        if keyword:
            ids = [row[0] for row in connection.execute(
                _ranked_search(category) + " SELECT id FROM ordered ORDER BY ordinal", {"keyword": keyword})]
        else:
            ids = [row[0] for row in connection.execute(
                f"SELECT id FROM words WHERE {_CATEGORIES[category]} ORDER BY learned,sort_order,id")]
        return get_entries_by_ids(connection, ids)


class PageReader:
    """A read-only connection created, used and closed on its owning thread."""
    def __init__(self, path):
        self.connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")

    def query_page(self, category, keyword="", page_number=1, anchor_id=None, page_size=PAGE_SIZE):
        return query_page(self.connection, category, keyword, page_number, anchor_id, page_size)

    def close(self):
        self.connection.close()

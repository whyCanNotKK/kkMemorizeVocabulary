-- Version 0 -> 1: the original vocabulary aggregates and application settings.
CREATE TABLE words (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL UNIQUE,
    important INTEGER NOT NULL DEFAULT 1 CHECK(important IN (0,1)),
    learned INTEGER NOT NULL DEFAULT 0 CHECK(learned IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE related_words (
    id INTEGER PRIMARY KEY,
    word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE TABLE senses (
    id INTEGER PRIMARY KEY,
    word_id INTEGER REFERENCES words(id) ON DELETE CASCADE,
    related_id INTEGER REFERENCES related_words(id) ON DELETE CASCADE,
    pos TEXT NOT NULL,
    translation TEXT NOT NULL,
    position INTEGER NOT NULL,
    CHECK((word_id IS NOT NULL) != (related_id IS NOT NULL))
);
CREATE INDEX related_owner ON related_words(word_id, position);
CREATE INDEX senses_word ON senses(word_id, position);
CREATE INDEX senses_related ON senses(related_id, position);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

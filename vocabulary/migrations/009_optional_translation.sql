-- Version 8 -> 9: optional translation text, with a required part of speech.
CREATE TABLE senses_new (
    id INTEGER PRIMARY KEY,
    word_id INTEGER REFERENCES words(id) ON DELETE CASCADE,
    related_id INTEGER REFERENCES related_words(id) ON DELETE CASCADE,
    pos TEXT NOT NULL CHECK(length(trim(pos)) > 0),
    translation TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL,
    CHECK((word_id IS NOT NULL) != (related_id IS NOT NULL))
);
INSERT INTO senses_new (id, word_id, related_id, pos, translation, position)
    SELECT id, word_id, related_id, pos, translation, position FROM senses;
DROP TABLE senses;
ALTER TABLE senses_new RENAME TO senses;
CREATE INDEX senses_word ON senses(word_id, position);
CREATE INDEX senses_related ON senses(related_id, position);

-- Version 1 -> 2: preserve each category's existing order.
ALTER TABLE words ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 1 CHECK(sort_order>0);
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY learned ORDER BY id) AS position
    FROM words
)
UPDATE words SET sort_order=(SELECT position FROM ranked WHERE ranked.id=words.id);
CREATE INDEX words_order ON words(learned,sort_order,id);

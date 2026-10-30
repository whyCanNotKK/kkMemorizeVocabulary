-- Version 4 -> 5: retain progress using only the six-field study model.
CREATE TABLE memorize_vocabulary_17day_v5 (
    word_id INTEGER PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    group_number INTEGER NOT NULL CHECK(group_number>0),
    stage INTEGER NOT NULL DEFAULT 1 CHECK(stage IN (0,1,2,4,7,15)),
    morning_done INTEGER NOT NULL DEFAULT 0 CHECK(morning_done IN (0,1)),
    afternoon_done INTEGER NOT NULL DEFAULT 0 CHECK(afternoon_done IN (0,1)),
    last_completed_on TEXT,
    CHECK(morning_done+afternoon_done<=1),
    CHECK((stage=1 AND last_completed_on IS NULL) OR
          (stage<>1 AND last_completed_on IS NOT NULL)),
    CHECK(stage<>0 OR (morning_done=0 AND afternoon_done=0))
);
INSERT INTO memorize_vocabulary_17day_v5
    (word_id,group_number,stage,morning_done,afternoon_done,last_completed_on)
SELECT word_id,group_number,CASE WHEN completed=1 THEN 0 ELSE stage END,
       morning_done,afternoon_done,last_completed_on
FROM memorize_vocabulary_17day;
DROP TABLE memorize_vocabulary_17day;
ALTER TABLE memorize_vocabulary_17day_v5 RENAME TO memorize_vocabulary_17day;
CREATE INDEX study_due ON memorize_vocabulary_17day(stage,last_completed_on);
DELETE FROM settings WHERE key GLOB 'study17.generated.*';
WITH highwater AS (
    SELECT MAX(value) AS value FROM (
        SELECT CAST(value AS INTEGER) AS value FROM settings WHERE key='study17.group_counter'
        UNION ALL
        SELECT MAX(group_number) FROM memorize_vocabulary_17day
    )
)
INSERT OR REPLACE INTO settings(key,value)
SELECT 'study17.group_counter', CAST(value AS TEXT) FROM highwater WHERE value IS NOT NULL;

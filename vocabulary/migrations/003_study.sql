-- Version 2 -> 3: the original study schema, converted to six fields in step 5.
CREATE TABLE memorize_vocabulary_17day (
    word_id INTEGER PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    group_number INTEGER NOT NULL CHECK(group_number>0),
    position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 10),
    enrolled_on TEXT NOT NULL,
    stage INTEGER NOT NULL DEFAULT 1 CHECK(stage IN (1,2,4,7,15)),
    next_due_on TEXT NOT NULL,
    morning_done INTEGER NOT NULL DEFAULT 0 CHECK(morning_done IN (0,1)),
    afternoon_done INTEGER NOT NULL DEFAULT 0 CHECK(afternoon_done IN (0,1)),
    last_completed_on TEXT,
    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
    UNIQUE(group_number,position)
);
CREATE INDEX study_due ON memorize_vocabulary_17day(completed,next_due_on);

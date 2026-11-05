CREATE TABLE study_task_history_seconds (
    id INTEGER PRIMARY KEY,
    task_date TEXT NOT NULL,
    afternoon INTEGER NOT NULL CHECK (afternoon IN (0,1)),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
    status INTEGER NOT NULL DEFAULT 0 CHECK (status IN (0,1)),
    new_total INTEGER NOT NULL CHECK (new_total >= 0),
    review_total INTEGER NOT NULL CHECK (review_total >= 0),
    new_done INTEGER NOT NULL DEFAULT 0 CHECK (new_done BETWEEN 0 AND new_total),
    review_done INTEGER NOT NULL DEFAULT 0 CHECK (review_done BETWEEN 0 AND review_total),
    UNIQUE (task_date, afternoon),
    CHECK ((status=0 AND ended_at IS NULL) OR (status=1 AND ended_at IS NOT NULL))
);
INSERT INTO study_task_history_seconds
    (id,task_date,afternoon,started_at,ended_at,duration_seconds,status,new_total,review_total,new_done,review_done)
SELECT id,task_date,afternoon,started_at,ended_at,
    duration_ms / 1000 + CASE WHEN duration_ms % 1000 >= 500 THEN 1 ELSE 0 END,
    status,new_total,review_total,new_done,review_done
FROM study_task_history;
DROP TABLE study_task_history;
ALTER TABLE study_task_history_seconds RENAME TO study_task_history;

CREATE TABLE study_task_history (
    id INTEGER PRIMARY KEY,
    task_date TEXT NOT NULL,
    afternoon INTEGER NOT NULL CHECK (afternoon IN (0,1)),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    status INTEGER NOT NULL DEFAULT 0 CHECK (status IN (0,1)),
    new_total INTEGER NOT NULL CHECK (new_total >= 0),
    review_total INTEGER NOT NULL CHECK (review_total >= 0),
    new_done INTEGER NOT NULL DEFAULT 0 CHECK (new_done BETWEEN 0 AND new_total),
    review_done INTEGER NOT NULL DEFAULT 0 CHECK (review_done BETWEEN 0 AND review_total),
    UNIQUE (task_date, afternoon),
    CHECK ((status=0 AND ended_at IS NULL) OR (status=1 AND ended_at IS NOT NULL))
);

-- Version 3 -> 4: consolidate dated draw markers into one last-draw date.
INSERT OR REPLACE INTO settings(key,value)
SELECT 'study_last_add_new_vocabulary', MAX(day)
FROM (
    SELECT substr(key,19) AS day FROM settings WHERE key GLOB 'study17.generated.*'
    UNION ALL
    SELECT value FROM settings WHERE key='study_last_add_new_vocabulary'
)
HAVING MAX(day) IS NOT NULL;
DELETE FROM settings WHERE key GLOB 'study17.generated.*';

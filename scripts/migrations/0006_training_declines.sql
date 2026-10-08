-- Отказы хранятся отдельно от регистраций: не занимают места и не создают долг.
BEGIN;
CREATE TABLE IF NOT EXISTS training_declines (
    training_id INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    display_name VARCHAR(100),
    PRIMARY KEY (training_id, user_id)
);
COMMIT;

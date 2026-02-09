-- Создание таблицы team_assignments для отслеживания статуса распределения игроков на каждую тренировку
CREATE TABLE IF NOT EXISTS team_assignments (
    id SERIAL PRIMARY KEY,
    training_id INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    team_assigned BOOLEAN NOT NULL DEFAULT FALSE,
    assigned_at TIMESTAMP NULL,
    UNIQUE(training_id, user_id)
);

-- Создание индексов для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_team_assignments_training_id ON team_assignments(training_id);
CREATE INDEX IF NOT EXISTS idx_team_assignments_user_id ON team_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_team_assignments_team_assigned ON team_assignments(team_assigned);

-- Удаление старого поля team_assigned из таблицы registrations
ALTER TABLE registrations DROP COLUMN IF EXISTS team_assigned;





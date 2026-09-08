-- Постоянный состав и поля шапки списка.
--
-- is_roster_member живёт на players, потому что это канонический реестр личностей:
-- user_id уникален, и строки есть у всех, включая заведённых вручную через
-- /add-player-by-username (у них отрицательный синтетический user_id).
-- Цвет и амплуа при этом НЕ дублируются: они берутся из user_preferences, куда их
-- уже пишет кнопка «Запомнить».
--
-- Поля шапки nullable: NULL означает «взять значение по умолчанию из app_settings».
--
-- Заготовка для первичного заполнения состава (выполнять осознанно, подобрав порог):
--   UPDATE players SET is_roster_member = true WHERE total_registrations >= 3;

BEGIN;

ALTER TABLE players
    ADD COLUMN IF NOT EXISTS is_roster_member BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE trainings
    ADD COLUMN IF NOT EXISTS end_time TIME,
    ADD COLUMN IF NOT EXISTS venue VARCHAR(200),
    ADD COLUMN IF NOT EXISTS signup_deadline_text TEXT;

CREATE INDEX IF NOT EXISTS idx_players_roster_member
    ON players (is_roster_member) WHERE is_roster_member;

COMMIT;

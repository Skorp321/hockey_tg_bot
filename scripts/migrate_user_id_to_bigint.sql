-- Миграция user_id с INTEGER на BIGINT для поддержки больших Telegram ID

-- Изменяем тип колонки в таблице registrations
ALTER TABLE registrations ALTER COLUMN user_id TYPE BIGINT;

-- Изменяем тип колонки в таблице players
ALTER TABLE players ALTER COLUMN user_id TYPE BIGINT;

-- Изменяем тип колонки в таблице user_preferences
ALTER TABLE user_preferences ALTER COLUMN user_id TYPE BIGINT;


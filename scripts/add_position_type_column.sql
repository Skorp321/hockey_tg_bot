-- Миграция для добавления колонки position_type в таблицу registrations
-- и обновления таблицы user_preferences

-- Добавляем колонку position_type в таблицу registrations
ALTER TABLE registrations ADD COLUMN position_type VARCHAR(10);

-- Добавляем колонку preferred_position_type в таблицу user_preferences
ALTER TABLE user_preferences ADD COLUMN preferred_position_type VARCHAR(10);

-- Создаем индексы для улучшения производительности
CREATE INDEX IF NOT EXISTS idx_registrations_position_type ON registrations(position_type);
CREATE INDEX IF NOT EXISTS idx_user_preferences_position_type ON user_preferences(preferred_position_type);

-- Добавляем комментарии к колонкам
COMMENT ON COLUMN registrations.position_type IS 'Амплуа игрока: forward (Нап) или defender (Зщ)';
COMMENT ON COLUMN user_preferences.preferred_position_type IS 'Предпочтительное амплуа игрока: forward (Нап) или defender (Зщ)';

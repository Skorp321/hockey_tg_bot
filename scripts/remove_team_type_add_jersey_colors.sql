-- Миграция для удаления team_type и добавления синих и желтых маек
-- Удаляем колонку team_type из таблицы registrations
ALTER TABLE registrations DROP COLUMN IF EXISTS team_type;

-- Удаляем колонку preferred_team_type из таблицы user_preferences
ALTER TABLE user_preferences DROP COLUMN IF EXISTS preferred_team_type;

-- Примечание: Для обновления enum JerseyType в PostgreSQL нужно:
-- 1. Создать новый тип enum с нужными значениями
-- 2. Изменить колонки на новый тип
-- 3. Удалить старый тип

-- Создаем новый тип enum для маек
DO $$ 
BEGIN
    -- Проверяем, существует ли уже новый тип
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'jerseytype_new') THEN
        CREATE TYPE jerseytype_new AS ENUM ('light', 'dark', 'blue', 'yellow');
    END IF;
END $$;

-- Обновляем колонку jersey_type в registrations
ALTER TABLE registrations 
    ALTER COLUMN jersey_type TYPE jerseytype_new 
    USING CASE 
        WHEN jersey_type::text = 'light' THEN 'light'::jerseytype_new
        WHEN jersey_type::text = 'dark' THEN 'dark'::jerseytype_new
        ELSE NULL
    END;

-- Обновляем колонку preferred_jersey_type в user_preferences
ALTER TABLE user_preferences 
    ALTER COLUMN preferred_jersey_type TYPE jerseytype_new 
    USING CASE 
        WHEN preferred_jersey_type::text = 'light' THEN 'light'::jerseytype_new
        WHEN preferred_jersey_type::text = 'dark' THEN 'dark'::jerseytype_new
        ELSE NULL
    END;

-- Удаляем старый тип enum
DROP TYPE IF EXISTS jerseytype CASCADE;

-- Переименовываем новый тип в старое имя
ALTER TYPE jerseytype_new RENAME TO jerseytype;

-- Обновляем колонки обратно на jerseytype
ALTER TABLE registrations 
    ALTER COLUMN jersey_type TYPE jerseytype 
    USING jersey_type::text::jerseytype;

ALTER TABLE user_preferences 
    ALTER COLUMN preferred_jersey_type TYPE jerseytype 
    USING preferred_jersey_type::text::jerseytype;

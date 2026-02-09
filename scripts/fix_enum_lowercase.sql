-- Миграция: приведение enum к нижнему регистру (light, dark, blue, yellow / forward, defender)
-- Решает ошибку: 'LIGHT' is not among the defined enum values
--
-- Как применить на проде:
--   1. Сделать бэкап БД: pg_dump -U USER -d DBNAME > backup_before_enum.sql
--   2. Подключиться к БД и выполнить:
--      psql -U USER -d DBNAME -f scripts/fix_enum_lowercase.sql
--   3. Перезапустить приложение (docker compose restart / systemctl restart ...)

BEGIN;

-- ========== 1. JerseyType (jerseytype) ==========
-- Создаём новый тип с значениями в нижнем регистре
CREATE TYPE jerseytype_new AS ENUM ('light', 'dark', 'blue', 'yellow');

-- registrations.jersey_type
ALTER TABLE registrations
  ALTER COLUMN jersey_type TYPE jerseytype_new
  USING (
    CASE lower(jersey_type::text)
      WHEN 'light'  THEN 'light'::jerseytype_new
      WHEN 'dark'  THEN 'dark'::jerseytype_new
      WHEN 'blue'  THEN 'blue'::jerseytype_new
      WHEN 'yellow' THEN 'yellow'::jerseytype_new
      ELSE NULL
    END
  );

-- user_preferences.preferred_jersey_type
ALTER TABLE user_preferences
  ALTER COLUMN preferred_jersey_type TYPE jerseytype_new
  USING (
    CASE lower(preferred_jersey_type::text)
      WHEN 'light'  THEN 'light'::jerseytype_new
      WHEN 'dark'  THEN 'dark'::jerseytype_new
      WHEN 'blue'  THEN 'blue'::jerseytype_new
      WHEN 'yellow' THEN 'yellow'::jerseytype_new
      ELSE NULL
    END
  );

-- Удаляем старый тип и переименовываем новый (колонки уже используют jerseytype_new, после RENAME тип называется jerseytype)
DROP TYPE jerseytype;
ALTER TYPE jerseytype_new RENAME TO jerseytype;


-- ========== 2. PositionType (positiontype) — только если тип уже есть в БД ==========
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'positiontype') THEN
    -- Создаём новый тип с lowercase
    CREATE TYPE positiontype_new AS ENUM ('forward', 'defender');

    -- registrations.position_type (если колонка уже enum positiontype)
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'registrations' AND column_name = 'position_type'
    ) THEN
      EXECUTE '
        ALTER TABLE registrations
          ALTER COLUMN position_type TYPE positiontype_new
          USING (
            CASE lower(position_type::text)
              WHEN ''forward''  THEN ''forward''::positiontype_new
              WHEN ''defender'' THEN ''defender''::positiontype_new
              ELSE NULL
            END
          )
      ';
    END IF;

    -- user_preferences.preferred_position_type
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'user_preferences' AND column_name = 'preferred_position_type'
    ) THEN
      EXECUTE '
        ALTER TABLE user_preferences
          ALTER COLUMN preferred_position_type TYPE positiontype_new
          USING (
            CASE lower(preferred_position_type::text)
              WHEN ''forward''  THEN ''forward''::positiontype_new
              WHEN ''defender'' THEN ''defender''::positiontype_new
              ELSE NULL
            END
          )
      ';
    END IF;

    DROP TYPE positiontype;
    ALTER TYPE positiontype_new RENAME TO positiontype;
  END IF;
END
$$;

COMMIT;

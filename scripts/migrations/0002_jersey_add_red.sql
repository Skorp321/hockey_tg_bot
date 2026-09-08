-- Цвета джерси: четыре -> пять, добавляется красный.
--
-- Тип пересоздаётся, а не расширяется через ALTER TYPE ... ADD VALUE: последнюю нельзя
-- выполнить внутри DO-блока, а без DO-блока не получится аккуратно проверить, существует
-- ли тип вообще. Пересоздание — та же схема, что в 0001, и одинаково работает независимо
-- от того, enum сейчас в колонке или varchar.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
               WHERE t.typname = 'jerseytype' AND e.enumlabel = 'red') THEN
        RAISE NOTICE 'jerseytype уже содержит red — пропускаем';
        RETURN;
    END IF;

    DROP TYPE IF EXISTS jerseytype_new;
    CREATE TYPE jerseytype_new AS ENUM ('light', 'dark', 'blue', 'yellow', 'red');

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'registrations' AND column_name = 'jersey_type') THEN
        EXECUTE $q$
            ALTER TABLE registrations
              ALTER COLUMN jersey_type TYPE jerseytype_new
              USING (CASE lower(jersey_type::text)
                       WHEN 'light'  THEN 'light'::jerseytype_new
                       WHEN 'dark'   THEN 'dark'::jerseytype_new
                       WHEN 'blue'   THEN 'blue'::jerseytype_new
                       WHEN 'yellow' THEN 'yellow'::jerseytype_new
                       WHEN 'red'    THEN 'red'::jerseytype_new
                       ELSE NULL
                     END)
        $q$;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'user_preferences' AND column_name = 'preferred_jersey_type') THEN
        EXECUTE $q$
            ALTER TABLE user_preferences
              ALTER COLUMN preferred_jersey_type TYPE jerseytype_new
              USING (CASE lower(preferred_jersey_type::text)
                       WHEN 'light'  THEN 'light'::jerseytype_new
                       WHEN 'dark'   THEN 'dark'::jerseytype_new
                       WHEN 'blue'   THEN 'blue'::jerseytype_new
                       WHEN 'yellow' THEN 'yellow'::jerseytype_new
                       WHEN 'red'    THEN 'red'::jerseytype_new
                       ELSE NULL
                     END)
        $q$;
    END IF;

    DROP TYPE IF EXISTS jerseytype;
    ALTER TYPE jerseytype_new RENAME TO jerseytype;
END $$;

COMMIT;

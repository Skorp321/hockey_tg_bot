-- Амплуа: две позиции (forward/defender) -> пять (лн, ц, пн, лз, пз).
--
-- Почему не ALTER TYPE ... ADD VALUE: значения не только добавляются, но и убираются,
-- поэтому тип пересоздаётся целиком.
--
-- Почему приведение идёт через ::text: на момент написания достоверно неизвестно, какого
-- типа колонка на проде. В дампе до перехода на PG16 это character varying(10), но
-- scripts/fix_enum_lowercase.sql, применённый при том переходе, делает ALTER COLUMN ... TYPE,
-- то есть колонка могла стать нативным enum. Приведение ::text работает одинаково в обоих
-- случаях, и это единственная форма, не требующая знать текущий тип.
--
-- Старые значения forward/defender очищаются в NULL: «нападающий» не говорит, левый он,
-- центральный или правый, поэтому переносить их наугад хуже, чем назначить заново.
-- Перед очисткой снимается копия в *_legacy на случай, если решение изменится.
--
-- Чтобы включить игроков в постоянный состав после назначения амплуа:
--   UPDATE players SET is_roster_member = true WHERE total_registrations >= 3;

BEGIN;

-- 1. Снимок старых значений. Идемпотентно: пишем только там, где ещё пусто.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'registrations' AND column_name = 'position_type') THEN
        ALTER TABLE registrations ADD COLUMN IF NOT EXISTS position_type_legacy TEXT;
        EXECUTE 'UPDATE registrations SET position_type_legacy = position_type::text
                 WHERE position_type_legacy IS NULL AND position_type IS NOT NULL';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'user_preferences' AND column_name = 'preferred_position_type') THEN
        ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS preferred_position_type_legacy TEXT;
        EXECUTE 'UPDATE user_preferences SET preferred_position_type_legacy = preferred_position_type::text
                 WHERE preferred_position_type_legacy IS NULL AND preferred_position_type IS NOT NULL';
    END IF;
END $$;

-- 2. Пересоздание типа и перевод колонок.
DO $$
BEGIN
    -- Уже применено? Тогда выходим: попытка удалить positiontype, на который ссылаются
    -- колонки, упала бы.
    IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
               WHERE t.typname = 'positiontype' AND e.enumlabel = 'lw') THEN
        RAISE NOTICE 'positiontype уже содержит новые значения — пропускаем';
        RETURN;
    END IF;

    DROP TYPE IF EXISTS positiontype_new;
    CREATE TYPE positiontype_new AS ENUM ('lw', 'c', 'rw', 'ld', 'rd');

    -- Ветки lw..rd тождественные: скрипт переживает повторный запуск после
    -- частичного применения, когда часть строк уже в новых значениях.
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'registrations' AND column_name = 'position_type') THEN
        EXECUTE $q$
            ALTER TABLE registrations
              ALTER COLUMN position_type TYPE positiontype_new
              USING (CASE lower(position_type::text)
                       WHEN 'lw' THEN 'lw'::positiontype_new
                       WHEN 'c'  THEN 'c'::positiontype_new
                       WHEN 'rw' THEN 'rw'::positiontype_new
                       WHEN 'ld' THEN 'ld'::positiontype_new
                       WHEN 'rd' THEN 'rd'::positiontype_new
                       ELSE NULL
                     END)
        $q$;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'user_preferences' AND column_name = 'preferred_position_type') THEN
        EXECUTE $q$
            ALTER TABLE user_preferences
              ALTER COLUMN preferred_position_type TYPE positiontype_new
              USING (CASE lower(preferred_position_type::text)
                       WHEN 'lw' THEN 'lw'::positiontype_new
                       WHEN 'c'  THEN 'c'::positiontype_new
                       WHEN 'rw' THEN 'rw'::positiontype_new
                       WHEN 'ld' THEN 'ld'::positiontype_new
                       WHEN 'rd' THEN 'rd'::positiontype_new
                       ELSE NULL
                     END)
        $q$;
    END IF;

    -- На этом шаге ни одна колонка на старый тип уже не ссылается.
    -- Если ссылается что-то ещё, DROP упадёт — и это правильное поведение.
    DROP TYPE IF EXISTS positiontype;
    ALTER TYPE positiontype_new RENAME TO positiontype;
END $$;

COMMIT;

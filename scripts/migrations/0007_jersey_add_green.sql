-- Шестая цветовая группа состава — зелёная.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'jerseytype') THEN
        ALTER TYPE jerseytype ADD VALUE IF NOT EXISTS 'green';
    END IF;
END $$;

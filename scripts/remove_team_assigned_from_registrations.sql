-- Удаляем колонку team_assigned из таблицы registrations
-- Это поле было перенесено в отдельную таблицу team_assignments

ALTER TABLE registrations DROP COLUMN IF EXISTS team_assigned;

-- Проверяем результат
SELECT column_name, data_type, is_nullable 
FROM information_schema.columns 
WHERE table_name = 'registrations' 
ORDER BY ordinal_position;




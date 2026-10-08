-- Тип события: тренировка или игра.
--
-- Строка, а не нативный enum: добавление нового типа не потребует ALTER TYPE.
-- Все уже созданные события — тренировки, поэтому DEFAULT 'training' заполняет
-- существующие строки корректно.

BEGIN;

ALTER TABLE trainings
    ADD COLUMN IF NOT EXISTS event_type VARCHAR(20) NOT NULL DEFAULT 'training';

COMMIT;

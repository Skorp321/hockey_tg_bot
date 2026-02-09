-- Создание таблицы scheduled_messages для управления запланированными сообщениями

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_text TEXT NOT NULL,
    send_immediately BOOLEAN NOT NULL DEFAULT 0,
    scheduled_time DATETIME,
    repeat_type VARCHAR(20) NOT NULL DEFAULT 'once',
    repeat_days VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT 1,
    last_sent_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Создание индексов для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_is_active ON scheduled_messages(is_active);
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_scheduled_time ON scheduled_messages(scheduled_time);
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_repeat_type ON scheduled_messages(repeat_type);


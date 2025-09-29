#!/bin/bash

# Скрипт для восстановления базы данных из бэкапа
# Использование: bash restore-db.sh <путь_к_бэкапу>

set -e

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Проверка аргументов
if [ -z "$1" ]; then
    echo -e "${RED}❌ Ошибка: Укажите путь к файлу бэкапа${NC}"
    echo "Использование: bash restore-db.sh <путь_к_бэкапу.sql.gz>"
    exit 1
fi

BACKUP_FILE=$1

# Проверка существования файла
if [ ! -f "$BACKUP_FILE" ]; then
    echo -e "${RED}❌ Файл бэкапа не найден: $BACKUP_FILE${NC}"
    exit 1
fi

echo -e "${YELLOW}⚠️  ВНИМАНИЕ: Это действие перезапишет текущую базу данных!${NC}"
read -p "Продолжить? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo -e "${YELLOW}Восстановление отменено${NC}"
    exit 0
fi

# Загружаем переменные окружения
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

echo -e "${YELLOW}📦 Восстановление базы данных из бэкапа...${NC}"

# Распаковываем, если файл сжат
if [[ $BACKUP_FILE == *.gz ]]; then
    echo -e "${YELLOW}Распаковка бэкапа...${NC}"
    gunzip -c $BACKUP_FILE | docker-compose exec -T db psql -U ${POSTGRES_USER:-user} ${POSTGRES_DB:-training_bot}
else
    cat $BACKUP_FILE | docker-compose exec -T db psql -U ${POSTGRES_USER:-user} ${POSTGRES_DB:-training_bot}
fi

echo -e "${GREEN}✅ База данных восстановлена из бэкапа${NC}"

# Перезапускаем приложение
echo -e "${YELLOW}🔄 Перезапуск приложения...${NC}"
docker-compose restart bot

echo -e "${GREEN}✅ Восстановление завершено успешно!${NC}"

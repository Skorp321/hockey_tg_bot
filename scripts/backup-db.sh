#!/bin/bash

# Скрипт для создания бэкапа базы данных PostgreSQL
# Использование: bash backup-db.sh

set -e

# Настройки
BACKUP_DIR="/home/deploy/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="hockey_db_backup_${TIMESTAMP}.sql"

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}📦 Создание бэкапа базы данных...${NC}"

# Создаем директорию для бэкапов
mkdir -p $BACKUP_DIR

# Загружаем переменные окружения
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Создаем бэкап
docker-compose exec -T db pg_dump -U ${POSTGRES_USER:-user} ${POSTGRES_DB:-training_bot} > "${BACKUP_DIR}/${BACKUP_FILE}"

# Сжимаем бэкап
gzip "${BACKUP_DIR}/${BACKUP_FILE}"

echo -e "${GREEN}✅ Бэкап создан: ${BACKUP_DIR}/${BACKUP_FILE}.gz${NC}"

# Удаляем бэкапы старше 30 дней
find $BACKUP_DIR -name "*.gz" -mtime +30 -delete
echo -e "${GREEN}✅ Старые бэкапы очищены${NC}"

# Показываем размер бэкапа
BACKUP_SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_FILE}.gz" | cut -f1)
echo -e "${YELLOW}📊 Размер бэкапа: ${BACKUP_SIZE}${NC}"

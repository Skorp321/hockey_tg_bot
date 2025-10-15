#!/bin/bash

# Скрипт для первоначальной настройки production сервера
# Использование: bash setup-server.sh

set -e  # Останавливаться при ошибках

echo "🚀 Начало настройки production сервера..."

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Проверка прав sudo
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}❌ Пожалуйста, запустите скрипт с правами sudo${NC}"
    exit 1
fi

echo -e "${YELLOW}📦 Обновление системы...${NC}"
apt update && apt upgrade -y

echo -e "${YELLOW}🐳 Установка Docker...${NC}"
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    echo -e "${GREEN}✅ Docker установлен${NC}"
else
    echo -e "${GREEN}✅ Docker уже установлен${NC}"
fi

echo -e "${YELLOW}🐳 Установка Docker Compose...${NC}"
if ! command -v docker-compose &> /dev/null; then
    apt install docker-compose -y
    echo -e "${GREEN}✅ Docker Compose установлен${NC}"
else
    echo -e "${GREEN}✅ Docker Compose уже установлен${NC}"
fi

echo -e "${YELLOW}📦 Установка Git...${NC}"
if ! command -v git &> /dev/null; then
    apt install git -y
    echo -e "${GREEN}✅ Git установлен${NC}"
else
    echo -e "${GREEN}✅ Git уже установлен${NC}"
fi

# Создание пользователя для деплоя
DEPLOY_USER="deploy"
echo -e "${YELLOW}👤 Создание пользователя для деплоя...${NC}"
if id "$DEPLOY_USER" &>/dev/null; then
    echo -e "${GREEN}✅ Пользователь $DEPLOY_USER уже существует${NC}"
else
    adduser --disabled-password --gecos "" $DEPLOY_USER
    usermod -aG docker $DEPLOY_USER
    echo -e "${GREEN}✅ Пользователь $DEPLOY_USER создан${NC}"
fi

# Создание директории для проекта
DEPLOY_PATH="/home/$DEPLOY_USER/hockey"
echo -e "${YELLOW}📁 Создание директории проекта...${NC}"
mkdir -p $DEPLOY_PATH
chown -R $DEPLOY_USER:$DEPLOY_USER $DEPLOY_PATH
echo -e "${GREEN}✅ Директория создана: $DEPLOY_PATH${NC}"

# Настройка SSH для пользователя deploy
echo -e "${YELLOW}🔑 Настройка SSH...${NC}"
mkdir -p /home/$DEPLOY_USER/.ssh
chmod 700 /home/$DEPLOY_USER/.ssh
touch /home/$DEPLOY_USER/.ssh/authorized_keys
chmod 600 /home/$DEPLOY_USER/.ssh/authorized_keys
chown -R $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER/.ssh

echo -e "${YELLOW}⚙️ Добавьте ваш публичный SSH ключ в файл:${NC}"
echo -e "${GREEN}/home/$DEPLOY_USER/.ssh/authorized_keys${NC}"
echo ""
echo -e "${YELLOW}Для этого выполните на локальной машине:${NC}"
echo -e "${GREEN}cat ~/.ssh/id_ed25519.pub | ssh root@your_server 'cat >> /home/$DEPLOY_USER/.ssh/authorized_keys'${NC}"

# Настройка firewall
echo -e "${YELLOW}🔥 Настройка firewall...${NC}"
if command -v ufw &> /dev/null; then
    ufw allow 22/tcp     # SSH
    ufw allow 5000/tcp   # Приложение
    ufw allow 80/tcp     # HTTP (если нужен)
    ufw allow 443/tcp    # HTTPS (если нужен)
    echo "y" | ufw enable
    echo -e "${GREEN}✅ Firewall настроен${NC}"
else
    apt install ufw -y
    ufw allow 22/tcp
    ufw allow 5000/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    echo "y" | ufw enable
    echo -e "${GREEN}✅ Firewall установлен и настроен${NC}"
fi

# Проверка версий
echo ""
echo -e "${GREEN}🎉 Установка завершена!${NC}"
echo ""
echo -e "${YELLOW}📊 Установленные версии:${NC}"
echo "Docker: $(docker --version)"
echo "Docker Compose: $(docker-compose --version)"
echo "Git: $(git --version)"
echo ""
echo -e "${YELLOW}📝 Следующие шаги:${NC}"
echo "1. Добавьте SSH ключ в /home/$DEPLOY_USER/.ssh/authorized_keys"
echo "2. Переключитесь на пользователя: sudo su - $DEPLOY_USER"
echo "3. Клонируйте репозиторий: git clone <your-repo-url> $DEPLOY_PATH"
echo "4. Создайте файл .env в $DEPLOY_PATH с production настройками"
echo "5. Запустите приложение: cd $DEPLOY_PATH && docker-compose up -d"
echo ""
echo -e "${YELLOW}🔐 Не забудьте настроить GitHub Secrets:${NC}"
echo "SSH_HOST=<ip вашего сервера>"
echo "SSH_USERNAME=$DEPLOY_USER"
echo "SSH_PRIVATE_KEY=<ваш приватный ключ>"
echo "SSH_PORT=22"
echo "DEPLOY_PATH=$DEPLOY_PATH"

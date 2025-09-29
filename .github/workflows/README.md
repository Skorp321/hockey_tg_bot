# CI/CD Pipeline - Инструкция по настройке

## Описание

Автоматический деплой приложения на production сервер через SSH при каждом push в ветку `main`.

## Настройка GitHub Secrets

Для работы пайплайна необходимо добавить следующие секреты в настройках репозитория GitHub:

### Переход к настройкам секретов:
1. Откройте ваш репозиторий на GitHub
2. Перейдите в `Settings` → `Secrets and variables` → `Actions`
3. Нажмите `New repository secret`

### Необходимые секреты:

#### 1. `SSH_HOST`
- **Описание**: IP-адрес или домен вашего production сервера
- **Пример**: `192.168.1.100` или `myserver.com`

#### 2. `SSH_USERNAME`
- **Описание**: Имя пользователя для SSH подключения
- **Пример**: `root`, `deploy`, `ubuntu`

#### 3. `SSH_PRIVATE_KEY`
- **Описание**: Приватный SSH ключ для подключения к серверу
- **Как получить**:
  ```bash
  # На локальной машине сгенерируйте ключ (если еще не создан)
  ssh-keygen -t ed25519 -C "github-actions-deploy"
  
  # Скопируйте публичный ключ на сервер
  ssh-copy-id -i ~/.ssh/id_ed25519.pub username@your_server
  
  # Скопируйте приватный ключ (всё содержимое файла)
  cat ~/.ssh/id_ed25519
  ```
- **Формат**: Полное содержимое приватного ключа, включая строки `-----BEGIN OPENSSH PRIVATE KEY-----` и `-----END OPENSSH PRIVATE KEY-----`

#### 4. `SSH_PORT`
- **Описание**: Порт SSH на сервере
- **Значение по умолчанию**: `22`

#### 5. `DEPLOY_PATH`
- **Описание**: Путь к директории проекта на сервере
- **Пример**: `/home/deploy/hockey` или `/var/www/hockey`

## Подготовка сервера

### 1. Установка необходимого ПО

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Установка Docker Compose
sudo apt install docker-compose -y

# Установка Git
sudo apt install git -y
```

### 2. Настройка проекта на сервере

```bash
# Создайте директорию для проекта
mkdir -p /home/deploy/hockey
cd /home/deploy/hockey

# Клонируйте репозиторий
git clone https://github.com/your-username/hockey.git .

# Создайте файл .env с производственными настройками
nano .env
```

### 3. Содержимое .env на сервере

```env
# База данных
POSTGRES_USER=your_prod_user
POSTGRES_PASSWORD=your_strong_password
POSTGRES_DB=training_bot

# Telegram Bot
TELEGRAM_TOKEN=your_production_bot_token

# Flask
SECRET_KEY=your_super_secret_production_key
FLASK_DEBUG=0

# Администраторы
ADMIN_IDS=your_telegram_id
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_admin_password

# Окружение
TZ=Europe/Moscow
PYTHONUNBUFFERED=1

# Порты
BOT_PORT=5000

# DNS
DNS_PRIMARY=8.8.8.8
DNS_SECONDARY=8.8.4.4

# Database URL
DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
```

### 4. Первый запуск на сервере

```bash
# Запустите контейнеры
docker-compose up -d

# Проверьте логи
docker-compose logs -f

# Проверьте статус
docker-compose ps
```

## Workflow Pipeline

### Этапы пайплайна:

1. **Test** - Запуск тестов и линтера
   - Установка зависимостей
   - Проверка кода с помощью flake8
   
2. **Deploy** - Деплой на production
   - Подключение к серверу по SSH
   - Получение последних изменений из git
   - Остановка контейнеров
   - Сборка новых образов
   - Запуск контейнеров
   - Очистка старых образов
   
3. **Check** - Проверка успешности деплоя
   - Проверка статуса контейнеров
   - Вывод логов при ошибке

## Триггеры запуска

- **Автоматический**: При каждом push в ветку `main`
- **Ручной**: Через вкладку `Actions` → выбрать workflow → `Run workflow`

## Мониторинг деплоя

1. Откройте GitHub → вкладка `Actions`
2. Выберите нужный workflow run
3. Смотрите логи каждого шага

## Откат (Rollback)

Если деплой прошел неудачно:

```bash
# Подключитесь к серверу
ssh username@your_server

# Перейдите в директорию проекта
cd /home/deploy/hockey

# Откатитесь на предыдущий коммит
git log --oneline  # Найдите нужный коммит
git reset --hard <commit-hash>

# Пересоберите и перезапустите
docker-compose down
docker-compose up -d --build
```

## Безопасность

- ✅ Все секреты хранятся в GitHub Secrets
- ✅ Приватный SSH ключ не попадает в репозиторий
- ✅ .env файл добавлен в .gitignore
- ⚠️ Регулярно меняйте пароли и ключи
- ⚠️ Используйте отдельного пользователя для деплоя (не root)

## Troubleshooting

### Проблема: SSH соединение не устанавливается
```bash
# Проверьте, что публичный ключ добавлен на сервер
cat ~/.ssh/authorized_keys
```

### Проблема: Git pull не работает
```bash
# Убедитесь, что у пользователя есть права на директорию
sudo chown -R $USER:$USER /home/deploy/hockey
```

### Проблема: Docker контейнеры не запускаются
```bash
# Проверьте логи
docker-compose logs

# Проверьте .env файл
cat .env
```

## Улучшения (TODO)

- [ ] Добавить Telegram/Slack уведомления о деплое
- [ ] Добавить автоматические бэкапы БД перед деплоем
- [ ] Добавить smoke tests после деплоя
- [ ] Настроить blue-green deployment
- [ ] Добавить метрики и мониторинг

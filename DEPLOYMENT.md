# 🚀 Руководство по развертыванию и CI/CD

## Обзор

Этот проект настроен с автоматическим CI/CD пайплайном для развертывания на production сервер через GitHub Actions и SSH.

## 📋 Содержание

1. [Быстрый старт](#быстрый-старт)
2. [Настройка сервера](#настройка-сервера)
3. [Настройка GitHub Secrets](#настройка-github-secrets)
4. [Workflows](#workflows)
5. [Управление деплоем](#управление-деплоем)
6. [Бэкапы](#бэкапы)
7. [Troubleshooting](#troubleshooting)

---

## Быстрый старт

### 1. Подготовка сервера

Загрузите и запустите скрипт настройки на вашем сервере:

```bash
# Подключитесь к серверу
ssh root@your-server-ip

# Скачайте скрипт настройки
wget https://raw.githubusercontent.com/your-username/hockey/main/scripts/setup-server.sh

# Запустите скрипт
sudo bash setup-server.sh
```

### 2. Настройка SSH ключей

На локальной машине:

```bash
# Сгенерируйте SSH ключ (если еще не создан)
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy

# Скопируйте публичный ключ на сервер
ssh-copy-id -i ~/.ssh/github_deploy.pub deploy@your-server-ip
```

### 3. Клонирование репозитория на сервер

```bash
# Переключитесь на пользователя deploy
sudo su - deploy

# Клонируйте репозиторий
cd /home/deploy
git clone https://github.com/your-username/hockey.git
cd hockey

# Создайте .env файл на основе примера
cp .env.production.example .env
nano .env  # Отредактируйте значения
```

### 4. Настройка GitHub Secrets

Перейдите в настройки вашего репозитория на GitHub:

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

Добавьте следующие секреты:

| Имя секрета | Значение | Описание |
|-------------|----------|----------|
| `SSH_HOST` | `192.168.1.100` | IP адрес вашего сервера |
| `SSH_USERNAME` | `deploy` | Имя пользователя для SSH |
| `SSH_PRIVATE_KEY` | `<содержимое ~/.ssh/github_deploy>` | Приватный SSH ключ |
| `SSH_PORT` | `22` | Порт SSH (по умолчанию 22) |
| `DEPLOY_PATH` | `/home/deploy/hockey` | Путь к проекту на сервере |

### 5. Первый деплой

```bash
# На сервере в директории проекта
docker-compose up -d

# Проверьте статус
docker-compose ps
docker-compose logs -f
```

---

## Настройка сервера

### Системные требования

- **ОС**: Ubuntu 20.04+ / Debian 11+
- **RAM**: Минимум 2GB
- **Диск**: Минимум 10GB свободного места
- **CPU**: 1+ ядро

### Автоматическая настройка

Используйте скрипт `scripts/setup-server.sh`, который:

- ✅ Устанавливает Docker и Docker Compose
- ✅ Устанавливает Git
- ✅ Создает пользователя для деплоя
- ✅ Настраивает SSH
- ✅ Настраивает firewall (UFW)

### Ручная настройка

<details>
<summary>Показать инструкции по ручной настройке</summary>

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Установка Docker Compose
sudo apt install docker-compose -y

# Установка Git
sudo apt install git -y

# Создание пользователя
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy

# Настройка SSH
sudo mkdir -p /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh
sudo touch /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys
sudo chown -R deploy:deploy /home/deploy/.ssh

# Настройка firewall
sudo ufw allow 22/tcp
sudo ufw allow 5000/tcp
sudo ufw enable
```
</details>

---

## Workflows

### 1. 🚀 Автоматический деплой (`deploy.yml`)

**Триггеры:**
- Push в ветку `main`
- Ручной запуск через Actions

**Этапы:**
1. **Test** - Запуск тестов и линтера
2. **Deploy** - Деплой на production
3. **Check** - Проверка статуса

**Использование:**

```bash
# Просто делайте push в main
git push origin main

# Или запустите вручную в GitHub:
# Actions → Deploy to Production → Run workflow
```

### 2. 🔧 Ручное управление (`manual-deploy.yml`)

Позволяет выполнять различные действия на production сервере.

**Действия:**
- `deploy` - Развернуть последнюю версию
- `rollback` - Откатиться на предыдущую версию
- `restart` - Перезапустить контейнеры
- `logs` - Просмотреть логи

**Использование:**

1. Перейдите в `Actions` → `Manual Deploy & Rollback`
2. Нажмите `Run workflow`
3. Выберите действие
4. (Опционально) Укажите хэш коммита для rollback
5. Нажмите `Run workflow`

### 3. 💾 Автоматические бэкапы (`backup.yml`)

**Триггер:**
- Каждый день в 3:00 UTC (6:00 МСК)
- Ручной запуск через Actions

**Что делает:**
- Создает дамп базы данных PostgreSQL
- Сжимает и сохраняет в `/home/deploy/backups/`
- Удаляет бэкапы старше 30 дней

---

## Управление деплоем

### Автоматический деплой

Просто делайте push в ветку `main`:

```bash
git add .
git commit -m "Update feature"
git push origin main
```

GitHub Actions автоматически:
1. Запустит тесты
2. Подключится к серверу
3. Получит последние изменения
4. Пересоберет Docker образы
5. Перезапустит контейнеры

### Ручной деплой

Через GitHub Actions:
1. `Actions` → `Manual Deploy & Rollback`
2. Выберите `deploy`
3. Запустите workflow

Или напрямую на сервере:

```bash
ssh deploy@your-server
cd /home/deploy/hockey
git pull origin main
docker-compose down
docker-compose up -d --build
```

### Откат (Rollback)

#### Через GitHub Actions:

1. `Actions` → `Manual Deploy & Rollback`
2. Выберите `rollback`
3. (Опционально) Укажите хэш коммита
4. Запустите workflow

#### Вручную на сервере:

```bash
ssh deploy@your-server
cd /home/deploy/hockey

# Посмотреть историю коммитов
git log --oneline -10

# Откатиться на конкретный коммит
git reset --hard <commit-hash>

# Пересобрать и перезапустить
docker-compose down
docker-compose up -d --build
```

### Перезапуск контейнеров

```bash
# Через GitHub Actions
Actions → Manual Deploy & Rollback → restart

# Или на сервере
ssh deploy@your-server
cd /home/deploy/hockey
docker-compose restart
```

### Просмотр логов

```bash
# Через GitHub Actions
Actions → Manual Deploy & Rollback → logs

# Или на сервере
ssh deploy@your-server
cd /home/deploy/hockey

# Все логи
docker-compose logs

# Последние 100 строк
docker-compose logs --tail=100

# В реальном времени
docker-compose logs -f

# Логи конкретного контейнера
docker-compose logs bot
docker-compose logs db
```

---

## Бэкапы

### Автоматические бэкапы

Настроены через GitHub Actions (`backup.yml`):
- Запускаются каждый день в 6:00 МСК
- Сохраняются в `/home/deploy/backups/`
- Старые бэкапы (>30 дней) удаляются автоматически

### Создание бэкапа вручную

```bash
ssh deploy@your-server
cd /home/deploy/hockey
bash scripts/backup-db.sh
```

### Восстановление из бэкапа

```bash
ssh deploy@your-server
cd /home/deploy/hockey

# Найти нужный бэкап
ls -lh /home/deploy/backups/

# Восстановить
bash scripts/restore-db.sh /home/deploy/backups/hockey_db_backup_YYYYMMDD_HHMMSS.sql.gz
```

⚠️ **ВНИМАНИЕ**: Восстановление перезапишет текущую базу данных!

### Скачивание бэкапа на локальную машину

```bash
# С сервера на локальную машину
scp deploy@your-server:/home/deploy/backups/hockey_db_backup_20250929_060000.sql.gz ~/Downloads/
```

---

## Мониторинг

### Проверка статуса контейнеров

```bash
ssh deploy@your-server
cd /home/deploy/hockey
docker-compose ps
```

Ожидаемый вывод:

```
NAME                COMMAND                  SERVICE   STATUS    PORTS
training_bot        "python run.py"          bot       Up        0.0.0.0:5000->5000/tcp
training_bot_db     "docker-entrypoint.s…"   db        Up        5432/tcp
```

### Проверка здоровья приложения

```bash
# Проверка health endpoint
curl http://your-server-ip:5000/health

# Ожидаемый ответ
{"status": "ok"}
```

### Использование ресурсов

```bash
# Использование ресурсов контейнерами
docker stats training_bot training_bot_db

# Использование диска
df -h

# Размер Docker образов
docker system df
```

---

## Troubleshooting

### ❌ Проблема: SSH соединение не устанавливается

**Решение:**

```bash
# Проверьте, что публичный ключ добавлен на сервер
ssh deploy@your-server "cat ~/.ssh/authorized_keys"

# Проверьте права доступа
ssh deploy@your-server "ls -la ~/.ssh/"

# Права должны быть:
# drwx------ .ssh/
# -rw------- .ssh/authorized_keys
```

### ❌ Проблема: GitHub Actions не может подключиться

**Проверьте:**

1. Правильность секретов в GitHub
2. Формат приватного ключа (должен включать `-----BEGIN ... KEY-----`)
3. Доступность сервера:

```bash
ping your-server-ip
telnet your-server-ip 22
```

### ❌ Проблема: Контейнеры не запускаются

**Решение:**

```bash
ssh deploy@your-server
cd /home/deploy/hockey

# Проверьте логи
docker-compose logs

# Проверьте .env файл
cat .env

# Проверьте docker-compose.yml
cat docker-compose.yml

# Попробуйте пересобрать
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### ❌ Проблема: База данных не доступна

**Решение:**

```bash
# Проверьте статус контейнера БД
docker-compose ps db

# Проверьте логи БД
docker-compose logs db

# Проверьте подключение
docker-compose exec db psql -U user -d training_bot -c "SELECT 1;"
```

### ❌ Проблема: Нехватка места на диске

**Решение:**

```bash
# Удалите неиспользуемые образы
docker image prune -a

# Удалите неиспользуемые volume
docker volume prune

# Полная очистка Docker
docker system prune -a --volumes

# Удалите старые бэкапы вручную
rm /home/deploy/backups/hockey_db_backup_*.sql.gz
```

### ❌ Проблема: Порт 5000 занят

**Решение:**

```bash
# Найдите процесс, использующий порт
sudo lsof -i :5000

# Или
sudo netstat -tlnp | grep 5000

# Остановите процесс
sudo kill -9 <PID>
```

---

## Безопасность

### Чеклист безопасности

- ✅ Все секреты в GitHub Secrets, а не в коде
- ✅ `.env` файл в `.gitignore`
- ✅ Используются сильные пароли (16+ символов)
- ✅ SSH ключи вместо паролей
- ✅ Firewall настроен и активен
- ✅ Отдельный пользователь для деплоя (не root)
- ✅ Регулярные бэкапы базы данных
- ✅ Docker образы обновляются регулярно

### Рекомендации

1. **Меняйте пароли регулярно** (каждые 3-6 месяцев)
2. **Используйте разные ключи** для разных окружений
3. **Мониторьте логи** на предмет подозрительной активности
4. **Обновляйте систему**:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```
5. **Используйте fail2ban** для защиты от брутфорса SSH

---

## Полезные команды

### Docker

```bash
# Статус контейнеров
docker-compose ps

# Логи
docker-compose logs -f

# Перезапуск
docker-compose restart

# Остановка
docker-compose down

# Запуск
docker-compose up -d

# Пересборка
docker-compose build --no-cache
docker-compose up -d

# Выполнить команду в контейнере
docker-compose exec bot bash
docker-compose exec db psql -U user -d training_bot
```

### Git

```bash
# Статус
git status

# История
git log --oneline -10

# Откат
git reset --hard <commit>

# Обновление
git pull origin main

# Откат файла
git checkout -- <file>
```

### Системные

```bash
# Использование диска
df -h

# Использование памяти
free -h

# Процессы
htop
# или
top

# Порты
sudo netstat -tlnp

# Логи системы
journalctl -xe
```

---

## Контакты и поддержка

При возникновении проблем:

1. Проверьте раздел [Troubleshooting](#troubleshooting)
2. Посмотрите логи: `docker-compose logs`
3. Проверьте статус в GitHub Actions
4. Откройте issue в репозитории

---

## Дополнительные ресурсы

- [Docker документация](https://docs.docker.com/)
- [GitHub Actions документация](https://docs.github.com/en/actions)
- [PostgreSQL документация](https://www.postgresql.org/docs/)
- [SSH ключи](https://www.ssh.com/academy/ssh/keygen)

---

**Версия документа**: 1.0  
**Последнее обновление**: 2025-09-29

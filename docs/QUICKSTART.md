# 🚀 Быстрый старт CI/CD

## За 5 минут до первого деплоя

### 1️⃣ Настройте сервер (1 минута)

```bash
# Подключитесь к серверу
ssh root@YOUR_SERVER_IP

# Загрузите и запустите скрипт
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/hockey/main/scripts/setup-server.sh | sudo bash
```

### 2️⃣ Создайте SSH ключ (1 минута)

```bash
# На локальной машине
ssh-keygen -t ed25519 -C "github-deploy" -f ~/.ssh/github_deploy

# Скопируйте на сервер
ssh-copy-id -i ~/.ssh/github_deploy.pub deploy@YOUR_SERVER_IP
```

### 3️⃣ Настройте GitHub Secrets (2 минуты)

В вашем репозитории: `Settings` → `Secrets and variables` → `Actions`

Добавьте 5 секретов:

| Имя | Значение |
|-----|----------|
| `SSH_HOST` | IP вашего сервера |
| `SSH_USERNAME` | `deploy` |
| `SSH_PRIVATE_KEY` | Содержимое `~/.ssh/github_deploy` |
| `SSH_PORT` | `22` |
| `DEPLOY_PATH` | `/home/deploy/hockey` |

### 4️⃣ Подготовьте проект на сервере (1 минута)

```bash
# Подключитесь как deploy
ssh deploy@YOUR_SERVER_IP

# Клонируйте репозиторий
git clone https://github.com/YOUR_USERNAME/hockey.git
cd hockey

# Настройте .env
cp .env.production.example .env
nano .env  # Замените значения на реальные

# Первый запуск
docker-compose up -d
```

### 5️⃣ Готово! 🎉

Теперь при каждом `git push origin main` ваше приложение автоматически обновится на сервере!

---

## Основные команды

### Просмотр логов
```bash
docker-compose logs -f
```

### Перезапуск
```bash
docker-compose restart
```

### Откат через GitHub Actions
1. `Actions` → `Manual Deploy & Rollback`
2. Выберите `rollback`
3. Запустите

### Создать бэкап
```bash
bash scripts/backup-db.sh
```

---

## 📚 Полная документация

Смотрите [DEPLOYMENT.md](DEPLOYMENT.md) для подробной информации.

## 🆘 Проблемы?

1. Проверьте логи: `docker-compose logs`
2. Проверьте статус: `docker-compose ps`
3. Смотрите [DEPLOYMENT.md#troubleshooting](DEPLOYMENT.md#troubleshooting)

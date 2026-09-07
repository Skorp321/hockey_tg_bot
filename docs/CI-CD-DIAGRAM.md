# 📊 Схема CI/CD процесса

## Архитектура деплоя

```
┌─────────────────────────────────────────────────────────────────────┐
│                         РАЗРАБОТЧИК                                 │
│                              ↓                                      │
│                    git push origin main                             │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       GITHUB REPOSITORY                             │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  main branch updated                                       │    │
│  │      ↓                                                     │    │
│  │  Trigger: GitHub Actions                                  │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       GITHUB ACTIONS                                │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │ JOB 1: Test                                              │    │
│  │  ├─ Checkout code                                        │    │
│  │  ├─ Setup Python 3.10                                    │    │
│  │  ├─ Install dependencies                                 │    │
│  │  └─ Run flake8 linter                                    │    │
│  └───────────────────────────────────────────────────────────┘    │
│                        ↓ (success)                                  │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │ JOB 2: Deploy                                            │    │
│  │  ├─ Connect to server via SSH                            │    │
│  │  ├─ cd /home/deploy/hockey                               │    │
│  │  ├─ git pull origin main                                 │    │
│  │  ├─ docker-compose down                                  │    │
│  │  ├─ docker-compose build --no-cache                      │    │
│  │  ├─ docker-compose up -d                                 │    │
│  │  └─ docker image prune -f                                │    │
│  └───────────────────────────────────────────────────────────┘    │
│                        ↓ (success)                                  │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │ JOB 3: Health Check                                      │    │
│  │  ├─ Check container status                               │    │
│  │  ├─ Verify application health                            │    │
│  │  └─ Show logs if failed                                  │    │
│  └───────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      PRODUCTION SERVER                              │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │  Docker Container: training_bot                         │      │
│  │  ├─ Python 3.10                                         │      │
│  │  ├─ FastAPI Web Server (port 5000)                      │      │
│  │  ├─ Telegram Bot                                        │      │
│  │  └─ Background tasks                                    │      │
│  └─────────────────────────────────────────────────────────┘      │
│                         ↕                                           │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │  Docker Container: training_bot_db                      │      │
│  │  ├─ PostgreSQL 13                                       │      │
│  │  ├─ Persistent Volume                                   │      │
│  │  └─ Health checks                                       │      │
│  └─────────────────────────────────────────────────────────┘      │
│                                                                     │
│  Network: bot_network (bridge)                                     │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      КОНЕЧНЫЕ ПОЛЬЗОВАТЕЛИ                          │
│  ├─ Web Interface (HTTP :5000)                                     │
│  └─ Telegram Bot                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Workflow диаграммы

### 1. Автоматический деплой (deploy.yml)

```
┌──────────────┐
│ Push to main │
└──────┬───────┘
       │
       ↓
┌──────────────┐
│  Run Tests   │
└──────┬───────┘
       │
       ↓ (pass)
┌──────────────┐      ┌─────────────────────────────┐
│    Deploy    │ ───→ │ SSH → Server                │
└──────┬───────┘      │ - git pull                  │
       │              │ - docker-compose down       │
       ↓              │ - docker-compose build      │
┌──────────────┐      │ - docker-compose up -d      │
│ Health Check │ ←─── │ - verify containers running │
└──────┬───────┘      └─────────────────────────────┘
       │
       ↓
┌──────────────┐
│   Success ✅  │
└──────────────┘
```

### 2. Ручной деплой/откат (manual-deploy.yml)

```
┌─────────────────────────────────────────────┐
│  Manual Trigger via GitHub Actions UI       │
│                                             │
│  Select action:                             │
│  ├─ deploy    → Full deployment             │
│  ├─ rollback  → Rollback to previous       │
│  ├─ restart   → Restart containers          │
│  └─ logs      → View logs                   │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────┴──────────┐
        ↓                     ↓
┌───────────────┐      ┌──────────────┐
│    Deploy     │      │   Rollback   │
├───────────────┤      ├──────────────┤
│ Same as auto  │      │ git reset    │
│ deployment    │      │ rebuild      │
│ workflow      │      │ restart      │
└───────────────┘      └──────────────┘
        ↓                     ↓
┌───────────────┐      ┌──────────────┐
│   Restart     │      │    Logs      │
├───────────────┤      ├──────────────┤
│ docker-compose│      │ Show last    │
│ restart       │      │ 100 lines    │
└───────────────┘      └──────────────┘
```

### 3. Автоматические бэкапы (backup.yml)

```
┌─────────────────────────┐
│  Cron: Daily at 6:00 AM │
│  or Manual Trigger      │
└────────┬────────────────┘
         │
         ↓
┌─────────────────────────┐
│  SSH → Server           │
│  cd /home/deploy/hockey │
└────────┬────────────────┘
         │
         ↓
┌─────────────────────────┐
│  Run backup-db.sh       │
│  ├─ pg_dump             │
│  ├─ gzip                │
│  ├─ save to backups/    │
│  └─ cleanup old (30d+)  │
└────────┬────────────────┘
         │
         ↓
┌─────────────────────────┐
│  Backup saved ✅         │
│  /home/deploy/backups/  │
│  hockey_db_*.sql.gz     │
└─────────────────────────┘
```

### 4. Health Check (health-check.yml)

```
┌──────────────────────────┐
│ Cron: Every 30 minutes   │
│ or Manual Trigger        │
└───────────┬──────────────┘
            │
            ↓
┌───────────────────────────┐
│ Check Container Status    │
│ docker-compose ps         │
└───────────┬───────────────┘
            │
            ↓
┌───────────────────────────┐
│ Check Application Health  │
│ curl http://...5000/health│
└───────────┬───────────────┘
            │
     ┌──────┴──────┐
     ↓             ↓
┌─────────┐   ┌─────────┐
│ Success │   │ Failure │
│    ✅    │   │    ❌    │
└─────────┘   └────┬────┘
                   │
                   ↓
            ┌──────────────┐
            │ Show logs    │
            │ Alert team   │
            └──────────────┘
```

## Структура файлов проекта

```
hockey/
├── .github/
│   └── workflows/
│       ├── deploy.yml           # Автоматический деплой
│       ├── manual-deploy.yml    # Ручное управление
│       ├── backup.yml           # Автоматические бэкапы
│       ├── health-check.yml     # Проверка здоровья
│       └── README.md            # Документация workflows
├── scripts/
│   ├── setup-server.sh          # Настройка сервера
│   ├── backup-db.sh             # Создание бэкапа БД
│   └── restore-db.sh            # Восстановление БД
├── app/                         # Код приложения
├── docker-compose.yml           # Docker конфигурация
├── Dockerfile                   # Docker образ
├── requirements.txt             # Python зависимости
├── .env.production.example      # Пример prod настроек
├── DEPLOYMENT.md                # Полная документация
├── QUICKSTART.md                # Быстрый старт
└── CI-CD-DIAGRAM.md             # Этот файл
```

## Последовательность деплоя

```
┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐
│ Dev │ →│ Git │ →│ GH  │ →│Test │ →│Build│ →│Start│ →│Check│
│Code │  │Push │  │Action│ │ ✓   │  │ 🐳  │  │ ✅  │  │ 🏥  │
└─────┘  └─────┘  └─────┘  └─────┘  └─────┘  └─────┘  └─────┘
  1min     <1sec    <1sec    ~30sec   ~60sec   ~10sec   ~5sec
                                                              
Total time: ~2-3 minutes от push до production 🚀
```

## Environments и Secrets

```
GitHub Secrets (зашифрованы)
├── SSH_HOST          → IP сервера
├── SSH_USERNAME      → deploy
├── SSH_PRIVATE_KEY   → ~/.ssh/github_deploy
├── SSH_PORT          → 22
└── DEPLOY_PATH       → /home/deploy/hockey
              ↓
        Используются в
              ↓
┌──────────────────────────────────┐
│  GitHub Actions Workflows        │
│  - deploy.yml                    │
│  - manual-deploy.yml             │
│  - backup.yml                    │
│  - health-check.yml              │
└──────────────────────────────────┘
              ↓
        SSH Connection
              ↓
┌──────────────────────────────────┐
│  Production Server               │
│  .env file (не в git!)           │
│  ├─ POSTGRES_USER                │
│  ├─ POSTGRES_PASSWORD            │
│  ├─ TELEGRAM_TOKEN               │
│  ├─ SECRET_KEY                   │
│  └─ ... other secrets            │
└──────────────────────────────────┘
```

## Rollback процесс

```
┌──────────────────┐
│ Trigger Rollback │
└────────┬─────────┘
         │
         ↓
┌─────────────────────────┐
│ Specify commit hash?    │
└─────┬──────────┬────────┘
      │ YES      │ NO
      ↓          ↓
┌──────────┐  ┌─────────────┐
│git reset │  │git reset    │
│--hard    │  │--hard HEAD~1│
│<hash>    │  │             │
└────┬─────┘  └──────┬──────┘
     │               │
     └───────┬───────┘
             ↓
    ┌─────────────────┐
    │ docker-compose  │
    │ down            │
    └────────┬────────┘
             ↓
    ┌─────────────────┐
    │ docker-compose  │
    │ build           │
    └────────┬────────┘
             ↓
    ┌─────────────────┐
    │ docker-compose  │
    │ up -d           │
    └────────┬────────┘
             ↓
    ┌─────────────────┐
    │ Rollback ✅      │
    │ Complete        │
    └─────────────────┘
```

## Мониторинг

```
┌────────────────────────────────────────┐
│       GitHub Actions Dashboard         │
│  ┌──────────────────────────────────┐  │
│  │ Deploy Status: ✅ Success         │  │
│  │ Last run: 2 hours ago            │  │
│  │ Duration: 2m 34s                 │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
              │
              ↓
┌────────────────────────────────────────┐
│       Production Server                │
│  ┌──────────────────────────────────┐  │
│  │ Container: training_bot          │  │
│  │ Status: Up 2 hours ✅             │  │
│  │ Health: /health → 200 OK         │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │ Container: training_bot_db       │  │
│  │ Status: Up 2 hours ✅             │  │
│  │ Health: pg_isready → OK          │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
              │
              ↓
┌────────────────────────────────────────┐
│       Automated Backups                │
│  ┌──────────────────────────────────┐  │
│  │ Last backup: Today 06:00         │  │
│  │ Size: 2.3 MB                     │  │
│  │ Retention: 30 days               │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
```

---

**Легенда:**
- 🚀 Деплой
- ✅ Успех
- ❌ Ошибка
- 🐳 Docker
- 💾 База данных
- 🔒 Безопасность
- 🏥 Health check
- 📦 Бэкап

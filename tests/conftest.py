"""Общая настройка тестов.

app/config.py бросает ValueError прямо на импорте, если нет TELEGRAM_TOKEN, поэтому
переменные окружения надо выставить ДО того, как импортируется что-либо из app.*.
По той же причине здесь нет импортов приложения на уровне модуля.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token-not-used")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")
# sqlite вместо постгреса: _to_async_url в app/database.py уже умеет его разворачивать
# в sqlite+aiosqlite, а aiosqlite и так есть в зависимостях.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

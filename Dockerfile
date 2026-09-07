# Используем официальный образ Python
FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# curl нужен healthcheck'у в docker-compose.
# gcc/libpq-dev больше не требуются: psycopg2 заменён на asyncpg с колёсами.
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1

# Открываем порты
EXPOSE 5000

# Запускаем приложение
CMD ["python", "run.py"] 
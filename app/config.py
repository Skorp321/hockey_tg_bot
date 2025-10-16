import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///training_bot.db')
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    SECRET_KEY = os.getenv('SECRET_KEY', 'your_secret_key_here')
    ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin')
    
    # Настройки для еженедельных постов
    CHANNEL_ID = os.getenv('CHANNEL_ID')  # ID канала или группы для постов
    WEEKLY_POST_ENABLED = os.getenv('WEEKLY_POST_ENABLED', 'true').lower() == 'true'

    # Проверяем наличие токена
    if not TELEGRAM_TOKEN:
        raise ValueError("No TELEGRAM_TOKEN set in environment variables") 
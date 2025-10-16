import asyncio
import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, TimedOut, BadRequest
from ..config import Config

# Настройка логирования
logger = logging.getLogger(__name__)

async def send_weekly_training_post(bot):
    """Отправляет еженедельный пост о тренировке в канал/группу"""
    try:
        if not Config.CHANNEL_ID:
            logger.warning("CHANNEL_ID не настроен, пропускаем отправку еженедельного поста")
            return False
            
        if not Config.WEEKLY_POST_ENABLED:
            logger.info("Еженедельные посты отключены")
            return False
        
        # Формируем сообщение
        message = """🏒 Тренеровка
Завтра, во вторник. 
Начало в 19.30-21.00
Стоимость 800-1000₽"""
        
        # Создаем клавиатуру с кнопкой записи
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💬 Запись у бота", 
                url="https://t.me/genhokmanager_bot?start=register"
            )]
        ])
        
        # Отправляем сообщение в канал/группу
        await bot.send_message(
            chat_id=Config.CHANNEL_ID,
            text=message,
            message_thread_id=Config.MESSAGE_THREAD_ID,
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Еженедельный пост о тренировке отправлен в канал {Config.CHANNEL_ID}")
        return True
        
    except NetworkError as e:
        logger.error(f"Сетевая ошибка при отправке еженедельного поста: {e}")
        return False
    except TimedOut as e:
        logger.error(f"Таймаут при отправке еженедельного поста: {e}")
        return False
    except BadRequest as e:
        logger.error(f"Некорректный запрос при отправке еженедельного поста: {e}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при отправке еженедельного поста: {e}")
        return False

def get_next_monday_11am():
    """Вычисляет следующий понедельник в 11:00"""
    now = datetime.now()
    
    # Находим следующий понедельник
    days_ahead = 0 - now.weekday()  # 0 = понедельник
    if days_ahead <= 0:  # Если сегодня понедельник или уже прошел
        days_ahead += 7  # Берем следующий понедельник
    
    next_monday = now + timedelta(days=days_ahead)
    next_monday = next_monday.replace(hour=11, minute=0, second=0, microsecond=0)
    
    return next_monday

async def weekly_post_scheduler(bot):
    """Планировщик еженедельных постов"""
    logger.info("🔄 Запуск планировщика еженедельных постов")
    
    while True:
        try:
            # Вычисляем время до следующего понедельника 11:00
            next_post_time = get_next_monday_11am()
            now = datetime.now()
            
            # Если время уже прошло сегодня, берем следующий понедельник
            if next_post_time <= now:
                next_post_time += timedelta(days=7)
            
            wait_seconds = (next_post_time - now).total_seconds()
            logger.info(f"⏰ Следующий еженедельный пост запланирован на {next_post_time.strftime('%d.%m.%Y %H:%M')}")
            logger.info(f"⏳ Ожидание {wait_seconds/3600:.1f} часов до следующего поста")
            
            # Ждем до времени отправки
            await asyncio.sleep(wait_seconds)
            
            # Отправляем пост
            success = await send_weekly_training_post(bot)
            if success:
                logger.info("✅ Еженедельный пост успешно отправлен")
            else:
                logger.error("❌ Не удалось отправить еженедельный пост")
                
        except Exception as e:
            logger.error(f"❌ Ошибка в планировщике еженедельных постов: {e}")
            # Ждем час перед повторной попыткой
            await asyncio.sleep(3600)

async def start_weekly_post_scheduler(bot):
    """Запускает планировщик еженедельных постов в фоновом режиме"""
    if not Config.WEEKLY_POST_ENABLED:
        logger.info("Еженедельные посты отключены в конфигурации")
        return
    
    if not Config.CHANNEL_ID:
        logger.warning("CHANNEL_ID не настроен, еженедельные посты не будут отправляться")
        return
    
    # Запускаем планировщик в фоновом режиме
    asyncio.create_task(weekly_post_scheduler(bot))
    logger.info("🚀 Планировщик еженедельных постов запущен")

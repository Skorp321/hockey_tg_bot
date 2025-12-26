import asyncio
import logging
import json
from datetime import datetime, timedelta
from telegram import Bot
from telegram.error import NetworkError, TimedOut, BadRequest
from ..config import Config
from ..database import db_session
from ..models import ScheduledMessage, RepeatType

logger = logging.getLogger(__name__)

async def send_scheduled_message(bot: Bot, message: ScheduledMessage):
    """Отправляет запланированное сообщение в канал"""
    try:
        if not Config.CHANNEL_ID:
            logger.warning("CHANNEL_ID не настроен, пропускаем отправку сообщения")
            return False
        
        # Валидация формата CHANNEL_ID
        try:
            channel_id_int = int(Config.CHANNEL_ID)
            if channel_id_int > 0:
                logger.warning(f"⚠️ CHANNEL_ID ({Config.CHANNEL_ID}) выглядит как личный чат. Для каналов/групп ID должен начинаться с -100")
        except (ValueError, TypeError):
            logger.error(f"❌ CHANNEL_ID имеет неверный формат: {Config.CHANNEL_ID}. Должно быть числовое значение.")
            return False
        
        # Отправляем сообщение в канал/группу
        send_params = {
            "chat_id": Config.CHANNEL_ID,
            "text": message.message_text
        }
        
        # Добавляем message_thread_id только если он задан (для топиков в супергруппах)
        if Config.MESSAGE_THREAD_ID:
            send_params["message_thread_id"] = int(Config.MESSAGE_THREAD_ID)
        
        await bot.send_message(**send_params)
        
        # Обновляем время последней отправки
        message.last_sent_at = datetime.now()
        db_session.commit()
        
        logger.info(f"✅ Запланированное сообщение #{message.id} отправлено в канал {Config.CHANNEL_ID}")
        return True
        
    except NetworkError as e:
        logger.error(f"Сетевая ошибка при отправке сообщения #{message.id}: {e}")
        return False
    except TimedOut as e:
        logger.error(f"Таймаут при отправке сообщения #{message.id}: {e}")
        return False
    except BadRequest as e:
        error_msg = str(e).lower()
        if 'chat not found' in error_msg:
            logger.error(f"❌ Канал не найден при отправке сообщения #{message.id}. Проверьте CHANNEL_ID={Config.CHANNEL_ID} и убедитесь, что бот добавлен в канал как администратор")
        elif 'bot was blocked' in error_msg:
            logger.error(f"❌ Бот заблокирован в канале при отправке сообщения #{message.id}")
        elif 'not enough rights' in error_msg:
            logger.error(f"❌ У бота недостаточно прав для отправки сообщения #{message.id}. Убедитесь, что бот является администратором с правами на отправку сообщений")
        else:
            logger.error(f"❌ Некорректный запрос при отправке сообщения #{message.id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при отправке сообщения #{message.id}: {e}")
        return False

def calculate_next_send_time(message: ScheduledMessage):
    """Вычисляет следующее время отправки для сообщения"""
    now = datetime.now()
    
    if message.repeat_type == RepeatType.ONCE:
        # Разовая отправка - возвращаем scheduled_time, если оно в будущем
        if message.scheduled_time and message.scheduled_time > now:
            return message.scheduled_time
        return None
    
    elif message.repeat_type == RepeatType.DAILY:
        # Ежедневно - если время прошло сегодня, берем завтра
        if message.scheduled_time:
            today_at_time = now.replace(
                hour=message.scheduled_time.hour,
                minute=message.scheduled_time.minute,
                second=0,
                microsecond=0
            )
            if today_at_time > now:
                return today_at_time
            else:
                return today_at_time + timedelta(days=1)
        return None
    
    elif message.repeat_type == RepeatType.WEEKLY:
        # Еженедельно - следующий выбранный день недели
        if not message.scheduled_time:
            return None
        
        repeat_days = message.get_repeat_days()
        if not repeat_days:
            return None
        
        # Текущий день недели (0 = понедельник, 6 = воскресенье)
        current_weekday = now.weekday()
        target_time = message.scheduled_time.time()
        
        # Ищем следующий день недели из списка
        for day_offset in range(7):
            check_day = (current_weekday + day_offset) % 7
            if str(check_day) in repeat_days:
                next_date = now + timedelta(days=day_offset)
                next_datetime = datetime.combine(next_date.date(), target_time)
                # Если это сегодня, но время уже прошло, берем следующий раз
                if day_offset == 0 and next_datetime <= now:
                    continue
                return next_datetime
        
        # Если не нашли в текущей неделе, берем первый день следующей недели
        first_day = min([int(d) for d in repeat_days])
        days_ahead = (first_day - current_weekday + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_date = now + timedelta(days=days_ahead)
        return datetime.combine(next_date.date(), target_time)
    
    elif message.repeat_type == RepeatType.MONTHLY:
        # Ежемесячно - следующее число месяца
        if not message.scheduled_time:
            return None
        
        target_day = message.scheduled_time.day
        target_time = message.scheduled_time.time()
        
        # Пробуем текущий месяц
        try:
            next_date = now.replace(day=target_day, hour=target_time.hour, 
                                   minute=target_time.minute, second=0, microsecond=0)
            if next_date > now:
                return next_date
        except ValueError:
            # Если такого дня нет в текущем месяце, переходим к следующему
            pass
        
        # Переходим к следующему месяцу
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1)
        else:
            next_month = now.replace(month=now.month + 1, day=1)
        
        # Пробуем установить нужный день
        while True:
            try:
                next_date = next_month.replace(day=target_day, hour=target_time.hour,
                                              minute=target_time.minute, second=0, microsecond=0)
                return next_date
            except ValueError:
                # Если такого дня нет (например, 31 февраля), берем последний день месяца
                next_month = next_month.replace(day=1) - timedelta(days=1)
                next_month = next_month.replace(day=1)
                if next_month.month == 12:
                    next_month = next_month.replace(year=next_month.year + 1, month=1)
                else:
                    next_month = next_month.replace(month=next_month.month + 1)
    
    return None

async def check_and_send_scheduled_messages(bot: Bot):
    """Проверяет и отправляет запланированные сообщения"""
    try:
        now = datetime.now()
        
        # Получаем все активные сообщения
        active_messages = db_session.query(ScheduledMessage)\
            .filter(ScheduledMessage.is_active == True)\
            .all()
        
        sent_count = 0
        for message in active_messages:
            try:
                # Для разовых сообщений проверяем scheduled_time
                if message.repeat_type == RepeatType.ONCE:
                    if message.scheduled_time and message.scheduled_time <= now:
                        # Проверяем, не было ли уже отправлено
                        if not message.last_sent_at:
                            success = await send_scheduled_message(bot, message)
                            if success:
                                sent_count += 1
                                # Деактивируем разовое сообщение после отправки
                                message.is_active = False
                                db_session.commit()
                
                # Для периодических сообщений проверяем scheduled_time
                else:
                    # Проверяем, нужно ли отправлять сейчас
                    should_send = False
                    
                    if not message.scheduled_time:
                        # Если scheduled_time не установлено, пропускаем
                        continue
                    
                    if message.scheduled_time <= now:
                        # Проверяем, не отправляли ли уже в этот период
                        if not message.last_sent_at:
                            should_send = True
                        else:
                            # Для daily проверяем, что прошло больше суток
                            if message.repeat_type == RepeatType.DAILY:
                                if (now - message.last_sent_at).total_seconds() >= 86400:
                                    should_send = True
                            # Для weekly проверяем, что прошло больше недели
                            elif message.repeat_type == RepeatType.WEEKLY:
                                if (now - message.last_sent_at).total_seconds() >= 604800:
                                    should_send = True
                            # Для monthly проверяем, что прошло больше месяца
                            elif message.repeat_type == RepeatType.MONTHLY:
                                if (now - message.last_sent_at).days >= 28:
                                    should_send = True
                        
                        if should_send:
                            success = await send_scheduled_message(bot, message)
                            if success:
                                sent_count += 1
                                # Обновляем scheduled_time для следующей отправки
                                message.scheduled_time = calculate_next_send_time(message)
                                db_session.commit()
                
            except Exception as e:
                logger.error(f"Ошибка при обработке сообщения #{message.id}: {e}")
                continue
        
        if sent_count > 0:
            logger.info(f"📨 Отправлено запланированных сообщений: {sent_count}")
        
        return sent_count
        
    except Exception as e:
        logger.error(f"❌ Ошибка в check_and_send_scheduled_messages: {e}")
        return 0

async def message_scheduler_task(bot: Bot):
    """Фоновая задача для проверки и отправки запланированных сообщений"""
    logger.info("🔄 Запуск планировщика запланированных сообщений")
    
    while True:
        try:
            await check_and_send_scheduled_messages(bot)
        except Exception as e:
            logger.error(f"❌ Ошибка в планировщике сообщений: {e}")
        
        # Проверяем каждую минуту
        await asyncio.sleep(60)

async def start_message_scheduler(bot: Bot):
    """Запускает планировщик запланированных сообщений в фоновом режиме"""
    if not Config.CHANNEL_ID:
        logger.warning("CHANNEL_ID не настроен, планировщик сообщений не будет работать")
        return
    
    # Запускаем планировщик в фоновом режиме
    asyncio.create_task(message_scheduler_task(bot))
    logger.info("🚀 Планировщик запланированных сообщений запущен")


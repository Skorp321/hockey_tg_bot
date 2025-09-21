from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, Application
from telegram.error import NetworkError, TimedOut, BadRequest
from datetime import datetime
import logging
from ..models import Training, Registration, UserPreferences, Player
from ..config import Config
from ..database import db_session

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def handle_telegram_errors(func):
    """Декоратор для обработки ошибок Telegram API"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await func(update, context)
        except NetworkError as e:
            logger.error(f"Сетевая ошибка в {func.__name__}: {e}")
            try:
                if update.callback_query:
                    await update.callback_query.answer("⚠️ Проблемы с сетью. Попробуйте позже.")
                elif update.message:
                    await update.message.reply_text("⚠️ Проблемы с сетью. Попробуйте позже.")
            except:
                pass
        except TimedOut as e:
            logger.error(f"Таймаут в {func.__name__}: {e}")
            try:
                if update.callback_query:
                    await update.callback_query.answer("⏰ Превышено время ожидания. Попробуйте позже.")
                elif update.message:
                    await update.message.reply_text("⏰ Превышено время ожидания. Попробуйте позже.")
            except:
                pass
        except BadRequest as e:
            logger.error(f"Некорректный запрос в {func.__name__}: {e}")
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Ошибка запроса. Попробуйте позже.")
                elif update.message:
                    await update.message.reply_text("❌ Ошибка запроса. Попробуйте позже.")
            except:
                pass
        except Exception as e:
            logger.error(f"Неожиданная ошибка в {func.__name__}: {e}")
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Произошла ошибка. Попробуйте позже.")
                elif update.message:
                    await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")
            except:
                pass
    return wrapper

def get_standard_keyboard():
    """Создает стандартную клавиатуру с основными кнопками"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Записаться на ближайшую тренировку", callback_data='register')],
        [InlineKeyboardButton("Показать расписание", callback_data='schedule')],
        [InlineKeyboardButton("Мои записи", callback_data='my_registrations')]
    ])

def get_info_keyboard():
    """Создает клавиатуру только с информационными кнопками (без записи)"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Показать расписание", callback_data='schedule')],
        [InlineKeyboardButton("Мои записи", callback_data='my_registrations')]
    ])

@handle_telegram_errors
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = get_standard_keyboard()
    await update.message.reply_text(
        'Добро пожаловать! Выберите действие:',
        reply_markup=reply_markup
    )

@handle_telegram_errors
async def register_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Получаем ближайшую доступную тренировку
    training = db_session.query(Training)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .first()
        
    if not training:
        await query.answer("Нет доступных тренировок")
        return
        
    # Проверяем, не записан ли уже пользователь
    existing_reg = db_session.query(Registration)\
        .filter_by(training_id=training.id, user_id=user_id)\
        .first()
        
    if existing_reg:
        await query.answer("Вы уже записаны на эту тренировку")
        return
    
    # Проверяем количество участников
    participants_count = db_session.query(Registration)\
        .filter_by(training_id=training.id)\
        .count()
    
    if participants_count >= training.max_participants:
        await query.answer("К сожалению, все места уже заняты")
        return
        
    # Получаем предпочтения пользователя
    user_prefs = db_session.query(UserPreferences).filter_by(user_id=user_id).first()
    
    # Создаем новую запись с предпочтениями пользователя
    # Используем display_name из предпочтений, если есть, иначе username
    display_name = user_prefs.display_name if user_prefs and user_prefs.display_name else None
    username = update.effective_user.username or "Без имени"
    
    registration = Registration(
        training_id=training.id,
        user_id=user_id,
        username=username,
        display_name=display_name,
        registered_at=datetime.now(),
        jersey_type=user_prefs.preferred_jersey_type if user_prefs else None,
        team_type=user_prefs.preferred_team_type if user_prefs else None,
        goalkeeper=user_prefs.goalkeeper if user_prefs else False
    )
    
    try:
        db_session.add(registration)
        
        # Обновляем или создаем запись в таблице players
        existing_player = db_session.query(Player).filter_by(user_id=user_id).first()
        if existing_player:
            # Обновляем существующего игрока
            existing_player.last_registration = datetime.now()
            existing_player.total_registrations += 1
            if display_name:
                existing_player.display_name = display_name
            existing_player.goalkeeper = user_prefs.goalkeeper if user_prefs else False
        else:
            # Создаем нового игрока
            new_player = Player(
                user_id=user_id,
                username=username,
                display_name=display_name,
                goalkeeper=user_prefs.goalkeeper if user_prefs else False,
                first_registration=datetime.now(),
                last_registration=datetime.now(),
                total_registrations=1
            )
            db_session.add(new_player)
        
        db_session.commit()
        await query.answer("Вы успешно записались на тренировку!")
        
        # Отправляем сообщение с подтверждением и деталями
        message = f"✅ Вы записаны на тренировку:\n"
        message += f"📅 {training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
        message += f"👥 Участников: {participants_count + 1}/{training.max_participants}"
        
        reply_markup = get_standard_keyboard()
        await query.message.reply_text(message, reply_markup=reply_markup)
    except Exception as e:
        db_session.rollback()
        print(f"Error during registration: {e}")
        await query.answer("Произошла ошибка при записи. Попробуйте позже.")

@handle_telegram_errors
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # Получаем все предстоящие тренировки
    trainings = db_session.query(Training)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .all()
    
    if not trainings:
        await query.answer("Нет запланированных тренировок")
        message = "В данный момент нет запланированных тренировок"
        reply_markup = get_standard_keyboard()
        await query.message.reply_text(message, reply_markup=reply_markup)
        return
    
    # Формируем сообщение с расписанием
    message = "📅 Расписание тренировок:\n\n"
    for training in trainings:
        participants = len(training.registrations)
        message += f"🕒 {training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
        message += f"👥 Участников: {participants}/{training.max_participants}\n\n"
    
    reply_markup = get_standard_keyboard()
    
    await query.answer()
    await query.message.reply_text(message, reply_markup=reply_markup)

@handle_telegram_errors
async def show_my_registrations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Получаем все регистрации пользователя на предстоящие тренировки
    registrations = db_session.query(Registration)\
        .join(Training)\
        .filter(Registration.user_id == user_id)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .all()
    
    if not registrations:
        await query.answer("У вас нет активных записей")
        message = "У вас нет активных записей на тренировки"
        reply_markup = get_standard_keyboard()
        await query.message.reply_text(message, reply_markup=reply_markup)
        return
    
    # Формируем сообщение со списком записей и кнопками отмены для каждой тренировки
    message = "🎯 Ваши записи на тренировки:\n\n"
    keyboard = []
    
    for reg in registrations:
        message += f"📅 {reg.training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
        
        # Если команда назначена, показываем полную информацию
        if reg.team_assigned:
            # Добавляем информацию о выбранной футболке и команде
            if reg.jersey_type:
                if reg.jersey_type.value == 'light':
                    jersey_info = "⚪"
                else:
                    jersey_info = "⚫"
                message += f"👕 {jersey_info}"
            else:
                message += f"👕 Футболка не выбрана"
            
            if reg.team_type:
                if reg.team_type.value == 'first':
                    team_info = "1️⃣"
                else:
                    team_info = "2️⃣"
                message += f" {team_info}\n"
            else:
                message += f" Команда не выбрана\n"
        else:
            message += f"👕 Команда не назначена\n"
        
        # Добавляем информацию об оплате
        if reg.paid:
            message += f"💰 Оплачено ✅\n"
        else:
            message += f"💰 Не оплачено ❌\n"
        
        message += "\n"
        
        # Добавляем кнопки для каждой тренировки
        training_buttons = []
        
        # Кнопка отмены записи
        training_buttons.append(InlineKeyboardButton(
            f"❌ Отменить запись",
            callback_data=f'cancel_{reg.id}'
        ))
        
        # Кнопка оплаты (только если не оплачено)
        if not reg.paid:
            training_buttons.append(InlineKeyboardButton(
                f"💰 Оплатил",
                callback_data=f'pay_{reg.id}'
            ))
        
        keyboard.append(training_buttons)
    
    # Добавляем кнопку просмотра участников и возврата в главное меню
    keyboard.append([InlineKeyboardButton("👥 Посмотреть участников", callback_data='view_participants')])
    keyboard.append([InlineKeyboardButton("🔙 Вернуться в меню", callback_data='start')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.answer()
    await query.message.reply_text(message, reply_markup=reply_markup)

async def mark_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Извлекаем ID регистрации из callback_data
    registration_id = int(query.data.split('_')[1])
    
    # Находим регистрацию
    registration = db_session.query(Registration).filter_by(id=registration_id, user_id=user_id).first()
    
    if not registration:
        await query.answer("Регистрация не найдена")
        return
    
    # Проверяем, что пользователь не оплатил уже
    if registration.paid:
        await query.answer("Вы уже отметили оплату для этой тренировки")
        return
    
    # Отмечаем как оплаченную
    registration.paid = True
    db_session.commit()
    
    await query.answer("✅ Оплата отмечена!")
    
    # Обновляем сообщение
    await show_my_registrations(update, context)

async def view_training_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Получаем все предстоящие тренировки
    trainings = db_session.query(Training)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .all()
    
    if not trainings:
        await query.answer("Нет предстоящих тренировок")
        message = "Нет предстоящих тренировок"
        reply_markup = get_standard_keyboard()
        await query.message.reply_text(message, reply_markup=reply_markup)
        return
    
    # Формируем сообщение со списком участников для каждой тренировки
    message = "👥 *Участники тренировок:*\n\n"
    
    for training in trainings:
        message += f"📅 *{training.date_time.strftime('%d.%m.%Y %H:%M')}*\n"
        message += f"👥 Участников: {len(training.registrations)}/{training.max_participants}\n\n"
        
        if not training.registrations:
            message += "Пока никто не записался\n\n"
            continue
        
        # Сортируем участников: сначала вратари, потом игроки по командам и майкам
        goalkeepers = []
        light_first_team = []
        dark_first_team = []
        light_second_team = []
        dark_second_team = []
        unassigned = []
        
        for reg in training.registrations:
            display_name = reg.display_name or reg.username or 'Без имени'
            
            if reg.goalkeeper:
                goalkeepers.append((display_name, reg.jersey_type, reg.paid))
            elif reg.team_assigned and reg.jersey_type and reg.team_type:
                if reg.jersey_type.value == 'light' and reg.team_type.value == 'first':
                    light_first_team.append((display_name, reg.paid))
                elif reg.jersey_type.value == 'dark' and reg.team_type.value == 'first':
                    dark_first_team.append((display_name, reg.paid))
                elif reg.jersey_type.value == 'light' and reg.team_type.value == 'second':
                    light_second_team.append((display_name, reg.paid))
                elif reg.jersey_type.value == 'dark' and reg.team_type.value == 'second':
                    dark_second_team.append((display_name, reg.paid))
            else:
                unassigned.append((display_name, reg.paid))
        
        # Выводим вратарей
        if goalkeepers:
            message += "🥅 *Вратари:*\n"
            for name, jersey_type, paid in goalkeepers:
                jersey_emoji = "⚪" if jersey_type and jersey_type.value == 'light' else "⚫"
                message += f"• {name} {jersey_emoji}\n"
            message += "\n"
        
        # Выводим игроков первой пятерки (светлые)
        if light_first_team:
            message += "⚪ *1-ая пятерка (светлые):*\n"
            for name, paid in light_first_team:
                message += f"• {name}\n"
            message += "\n"
        
        # Выводим игроков первой пятерки (темные)
        if dark_first_team:
            message += "⚫ *1-ая пятерка (темные):*\n"
            for name, paid in dark_first_team:
                message += f"• {name}\n"
            message += "\n"
        
        # Выводим игроков второй пятерки (светлые)
        if light_second_team:
            message += "⚪ *2-ая пятерка (светлые):*\n"
            for name, paid in light_second_team:
                message += f"• {name}\n"
            message += "\n"
        
        # Выводим игроков второй пятерки (темные)
        if dark_second_team:
            message += "⚫ *2-ая пятерка (темные):*\n"
            for name, paid in dark_second_team:
                message += f"• {name}\n"
            message += "\n"
        
        # Выводим нераспределенных участников
        if unassigned:
            message += "❓ *Нераспределенные:*\n"
            for name, paid in unassigned:
                message += f"• {name}\n"
            message += "\n"
        
        message += "---\n\n"
    
    # Создаем клавиатуру с кнопкой возврата
    keyboard = [
        [InlineKeyboardButton("🔙 Вернуться в меню", callback_data='start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.answer()
    await query.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Получаем ID регистрации из callback_data
    reg_id = int(query.data.split('_')[1])
    
    # Находим и удаляем регистрацию
    registration = db_session.query(Registration)\
        .filter_by(id=reg_id, user_id=user_id)\
        .first()
    
    if registration:
        # Сохраняем display_name в UserPreferences перед удалением регистрации
        if registration.display_name:
            user_prefs = db_session.query(UserPreferences).filter_by(user_id=user_id).first()
            if not user_prefs:
                user_prefs = UserPreferences(user_id=user_id)
                db_session.add(user_prefs)
            user_prefs.display_name = registration.display_name
        
        db_session.delete(registration)
        db_session.commit()
        await query.answer("Запись отменена")
        message = "Ваша запись успешно отменена"
        reply_markup = get_standard_keyboard()
        await query.message.reply_text(message, reply_markup=reply_markup)
    else:
        await query.answer("Запись не найдена")

async def view_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для просмотра участников ближайшей тренировки"""
    # Получаем ближайшую тренировку
    training = db_session.query(Training)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .first()
    
    if not training:
        message = "Нет запланированных тренировок."
        reply_markup = get_standard_keyboard()
        await update.message.reply_text(message, reply_markup=reply_markup)
        return
    
    # Получаем список участников
    registrations = db_session.query(Registration)\
        .filter_by(training_id=training.id)\
        .all()
    
    # Формируем сообщение
    message = f"📅 Тренировка {training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
    message += f"👥 Участники ({len(registrations)}/{training.max_participants}):\n\n"
    
    if registrations:
        for i, reg in enumerate(registrations, 1):
            # Используем display_name если есть, иначе username
            display_name = reg.display_name or reg.username or "Без имени"
            
            # Если команда назначена, показываем полную информацию
            if reg.team_assigned:
                # Добавляем информацию о выбранной футболке и команде
                if reg.jersey_type:
                    if reg.jersey_type.value == 'light':
                        jersey_info = "⚪"
                    else:
                        jersey_info = "⚫"
                    message += f"{i}. {display_name} {jersey_info}"
                else:
                    message += f"{i}. {display_name}"
                
                if reg.team_type:
                    if reg.team_type.value == 'first':
                        team_info = "1️⃣"
                    else:
                        team_info = "2️⃣"
                    message += f" {team_info}\n"
                else:
                    message += "\n"
            else:
                # Если команда не назначена, показываем только фамилию
                # Извлекаем фамилию из полного имени (последнее слово)
                surname = display_name.split()[-1] if display_name else "Без имени"
                message += f"{i}. {surname}\n"
    else:
        message += "Пока никто не записался"
    
    reply_markup = get_info_keyboard()
    await update.message.reply_text(message, reply_markup=reply_markup)

async def show_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список доступных команд"""
    user_id = update.effective_user.id
    
    # Базовые команды для всех пользователей
    commands = """
📋 Доступные команды:

/start - Начать работу с ботом
/commands - Показать список команд
/participants - Просмотр списка участников ближайшей тренировки
"""
    
    # Дополнительные команды для администраторов
    if user_id in Config.ADMIN_IDS:
        admin_commands = """
👑 Команды администратора:

"""
        commands += admin_commands
    
    reply_markup = get_standard_keyboard()
    await update.message.reply_text(commands, reply_markup=reply_markup)

# Добавим новый обработчик для возврата в главное меню
async def return_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    reply_markup = get_standard_keyboard()
    await query.answer()
    await query.message.reply_text('Выберите действие:', reply_markup=reply_markup)

async def start_bot():
    token = Config.TELEGRAM_TOKEN
    if not token:
        raise ValueError("TELEGRAM_TOKEN не установлен в переменных окружения")
    
    # Создаем приложение с настройками для обработки сетевых ошибок
    application = Application.builder().token(token).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("commands", show_commands))
    application.add_handler(CommandHandler("participants", view_participants))
    application.add_handler(CallbackQueryHandler(register_training, pattern="^register$"))
    application.add_handler(CallbackQueryHandler(show_schedule, pattern="^schedule$"))
    application.add_handler(CallbackQueryHandler(show_my_registrations, pattern="^my_registrations$"))
    application.add_handler(CallbackQueryHandler(cancel_registration, pattern="^cancel_\d+$"))
    application.add_handler(CallbackQueryHandler(mark_payment, pattern="^pay_\d+$"))
    application.add_handler(CallbackQueryHandler(view_training_participants, pattern="^view_participants$"))
    application.add_handler(CallbackQueryHandler(return_to_start, pattern="^start$"))
    
    # Настройки для polling с обработкой ошибок
    try:
        # Запускаем бота без блокировки
        await application.initialize()
        await application.start()
        
        # Настраиваем polling с параметрами для обработки сетевых ошибок
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=['message', 'callback_query'],
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30
        )
        
        print("✅ Telegram бот успешно запущен")
        return application
        
    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        # Пытаемся корректно завершить приложение
        try:
            await application.stop()
            await application.shutdown()
        except:
            pass
        raise 
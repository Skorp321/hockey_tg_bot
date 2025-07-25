from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, Application
from datetime import datetime
from ..models import Training, Registration
from ..config import Config
from ..database import db_session

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = get_standard_keyboard()
    await update.message.reply_text(
        'Добро пожаловать! Выберите действие:',
        reply_markup=reply_markup
    )

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
        
    # Создаем новую запись
    registration = Registration(
        training_id=training.id,
        user_id=user_id,
        username=update.effective_user.username or "Без имени",
        registered_at=datetime.now()
    )
    
    try:
        db_session.add(registration)
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
        
        # Добавляем информацию о выбранной футболке
        if reg.jersey_type:
            if reg.jersey_type.value == 'light':
                jersey_info = "⚪"
            else:
                jersey_info = "⚫"
            message += f"👕 {jersey_info}\n"
        else:
            message += f"👕 Футболка не выбрана\n"
        
        message += "\n"
        
        keyboard.append([InlineKeyboardButton(
            f"❌ Отменить запись на {reg.training.date_time.strftime('%d.%m.%Y %H:%M')}",
            callback_data=f'cancel_{reg.id}'
        )])
    
    # Добавляем кнопку возврата в главное меню
    keyboard.append([InlineKeyboardButton("🔙 Вернуться в меню", callback_data='start')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.answer()
    await query.message.reply_text(message, reply_markup=reply_markup)

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
            username = reg.username or "Без имени"
            
            # Добавляем информацию о выбранной футболке
            if reg.jersey_type:
                if reg.jersey_type.value == 'light':
                    jersey_info = "⚪"
                else:
                    jersey_info = "⚫"
                message += f"{i}. {username} {jersey_info}\n"
            else:
                message += f"{i}. {username}\n"
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
    application = Application.builder().token(token).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("commands", show_commands))
    application.add_handler(CommandHandler("participants", view_participants))
    application.add_handler(CallbackQueryHandler(register_training, pattern="^register$"))
    application.add_handler(CallbackQueryHandler(show_schedule, pattern="^schedule$"))
    application.add_handler(CallbackQueryHandler(show_my_registrations, pattern="^my_registrations$"))
    application.add_handler(CallbackQueryHandler(cancel_registration, pattern="^cancel_\d+$"))
    application.add_handler(CallbackQueryHandler(return_to_start, pattern="^start$"))
    
    # Запускаем бота без блокировки
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    return application  # Возвращаем экземпляр приложения для корректного завершения 
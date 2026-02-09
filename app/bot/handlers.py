from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, Application
from telegram.error import NetworkError, TimedOut, BadRequest, Forbidden, Conflict
from datetime import datetime
import logging
import re
from ..models import Training, Registration, UserPreferences, Player, PositionType, TeamAssignment
from ..config import Config
from ..database import db_session
from .weekly_posts import send_weekly_training_post

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def escape_markdown(text):
    """Экранирует специальные символы Markdown"""
    if not text:
        return text
    # Экранируем специальные символы для обычного Markdown (parse_mode='Markdown')
    # Для обычного Markdown нужно экранировать: _ * [ ` 
    special_chars = ['_', '*', '[', '`']
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    return text

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
    """Создает стандартную клавиатуру с основными кнопками (без записи на тренировки)"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Показать расписание", callback_data='schedule')],
        [InlineKeyboardButton("Мои записи", callback_data='my_registrations')]
    ])

def get_info_keyboard():
    """Создает клавиатуру только с информационными кнопками (без записи)"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Показать расписание", callback_data='schedule')],
        [InlineKeyboardButton("Мои записи", callback_data='my_registrations')]
    ])

def update_temporary_user_id(real_user_id, username):
    """
    Обновляет временный user_id на реальный, когда пользователь впервые взаимодействует с ботом.
    Временные user_id - это отрицательные числа, созданные на основе hash от username.
    """
    if not username:
        return
    
    try:
        # Ищем игрока с таким же username и отрицательным (временным) user_id
        temp_player = db_session.query(Player)\
            .filter(Player.username == username)\
            .filter(Player.user_id < 0)\
            .first()
        
        if temp_player:
            logger.info(f"Найден временный игрок с username={username}, обновляем user_id с {temp_player.user_id} на {real_user_id}")
            
            # Обновляем все регистрации этого игрока
            registrations = db_session.query(Registration)\
                .filter_by(user_id=temp_player.user_id)\
                .all()
            
            for reg in registrations:
                reg.user_id = real_user_id
                logger.info(f"Обновлена регистрация {reg.id} на тренировку {reg.training_id}")
            
            # Обновляем предпочтения пользователя, если есть
            temp_prefs = db_session.query(UserPreferences)\
                .filter_by(user_id=temp_player.user_id)\
                .first()
            
            if temp_prefs:
                # Проверяем, нет ли уже предпочтений с реальным user_id
                real_prefs = db_session.query(UserPreferences)\
                    .filter_by(user_id=real_user_id)\
                    .first()
                
                if not real_prefs:
                    temp_prefs.user_id = real_user_id
                else:
                    # Если есть, удаляем временные предпочтения
                    db_session.delete(temp_prefs)
            
            # Проверяем, нет ли уже игрока с реальным user_id
            real_player = db_session.query(Player)\
                .filter_by(user_id=real_user_id)\
                .first()
            
            if not real_player:
                # Обновляем user_id временного игрока
                temp_player.user_id = real_user_id
            else:
                # Если есть, объединяем данные и удаляем временного игрока
                real_player.total_registrations += temp_player.total_registrations
                if temp_player.first_registration < real_player.first_registration:
                    real_player.first_registration = temp_player.first_registration
                db_session.delete(temp_player)
            
            db_session.commit()
            logger.info(f"Успешно обновлен user_id для игрока {username}")
            
    except Exception as e:
        logger.error(f"Ошибка при обновлении временного user_id: {e}")
        db_session.rollback()

@handle_telegram_errors
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Обновляем временный user_id на реальный, если необходимо
    user_id = update.effective_user.id
    username = update.effective_user.username
    if username:
        update_temporary_user_id(user_id, username)
    
    reply_markup = get_standard_keyboard()
    await update.message.reply_text(
        'Добро пожаловать! Выберите действие:',
        reply_markup=reply_markup
    )

@handle_telegram_errors
async def register_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    # Обновляем временный user_id на реальный, если необходимо
    if username:
        update_temporary_user_id(user_id, username)
    
    # Извлекаем ID тренировки из callback_data (формат: register_123)
    training_id = int(query.data.split('_')[1])
    
    # Получаем выбранную тренировку
    training = db_session.query(Training)\
        .filter(Training.id == training_id)\
        .filter(Training.date_time > datetime.now())\
        .first()
        
    if not training:
        await query.answer("Тренировка не найдена или уже прошла")
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
    
    # Обновляем временный user_id на реальный, если необходимо
    user_id = update.effective_user.id
    username = update.effective_user.username
    if username:
        update_temporary_user_id(user_id, username)
    
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
    
    # Создаем клавиатуру с кнопками для записи на каждую тренировку (до 5)
    keyboard = []
    for training in trainings[:5]:  # Ограничиваем 5 тренировками
        participants = len(training.registrations)
        date_str = training.date_time.strftime('%d.%m %H:%M')
        button_text = f"📅 {date_str} ({participants}/{training.max_participants})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f'register_{training.id}')])
    
    # Добавляем кнопку возврата в меню
    keyboard.append([InlineKeyboardButton("🔙 Вернуться в меню", callback_data='start')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.answer()
    await query.message.reply_text(message, reply_markup=reply_markup)

@handle_telegram_errors
async def show_my_registrations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    # Обновляем временный user_id на реальный, если необходимо
    if username:
        update_temporary_user_id(user_id, username)
    
    # Получаем предстоящие тренировки
    upcoming_registrations = db_session.query(Registration)\
        .join(Training)\
        .filter(Registration.user_id == user_id)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .all()
    
    # Получаем прошедшие неоплаченные тренировки (только для не-вратарей)
    past_unpaid_registrations = db_session.query(Registration)\
        .join(Training)\
        .filter(Registration.user_id == user_id)\
        .filter(Training.date_time <= datetime.now())\
        .filter(Registration.paid == False)\
        .filter(Registration.goalkeeper == False)\
        .order_by(Training.date_time)\
        .all()
    
    # Объединяем списки
    registrations = upcoming_registrations + past_unpaid_registrations
    
    if not registrations:
        await query.answer("У вас нет активных записей")
        message = "У вас нет активных записей на тренировки"
        reply_markup = get_standard_keyboard()
        await query.message.reply_text(message, reply_markup=reply_markup)
        return
    
    # Формируем сообщение со списком записей
    message = "🎯 Ваши записи на тренировки:\n\n"
    
    for i, reg in enumerate(registrations, 1):
        message += f"{i}. 📅 {reg.training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
        
        # Получаем статус team_assigned из таблицы TeamAssignment
        team_assignment = db_session.query(TeamAssignment)\
            .filter_by(training_id=reg.training_id, user_id=reg.user_id)\
            .first()
        team_assigned = team_assignment.team_assigned if team_assignment else False
        
        # Если команда назначена, показываем полную информацию
        if team_assigned:
            # Добавляем информацию о выбранной футболке
            jersey_emojis = {
                'light': '⚪',
                'dark': '⚫',
                'blue': '🔵',
                'yellow': '🟡'
            }
            if reg.jersey_type:
                jersey_info = jersey_emojis.get(reg.jersey_type.value, '👕')
                message += f"   👕 {jersey_info}"
            else:
                message += f"   👕 Футболка не выбрана"
            
            # Добавляем информацию об амплуа для полевых игроков
            if not reg.goalkeeper and reg.position_type:
                if reg.position_type.value == 'forward':
                    position_info = " - Нап"
                else:
                    position_info = " - Зщ"
                message += f"{position_info}"
            
            message += "\n"
        else:
            message += f"   👕 Команда не назначена\n"
        
        # Добавляем информацию об оплате (только для не-вратарей)
        if not reg.goalkeeper:
            if reg.paid:
                message += f"   💰 Оплачено ✅\n"
            else:
                message += f"   💰 Не оплачено ❌\n"
        else:
            message += f"   🥅 Вратарь\n"
        
        message += "\n"
    
    # Создаем компактную клавиатуру с общими действиями
    keyboard = []
    
    # Если есть неоплаченные записи (не вратари), добавляем кнопку оплаты
    unpaid_registrations = [reg for reg in registrations if not reg.paid and not reg.goalkeeper]
    if unpaid_registrations:
        keyboard.append([InlineKeyboardButton("💰 Оплатил", callback_data='mark_payment')])
    
    # Кнопка отмены записи (показываем только если есть предстоящие тренировки)
    if upcoming_registrations:
        keyboard.append([InlineKeyboardButton("❌ Отменить запись", callback_data='cancel_registration')])
    
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
        
        # Сортируем участников: сначала вратари, потом игроки по майкам
        goalkeepers = []
        light_players = []
        dark_players = []
        blue_players = []
        yellow_players = []
        unassigned = []
        
        jersey_emojis = {
            'light': '⚪',
            'dark': '⚫',
            'blue': '🔵',
            'yellow': '🟡'
        }
        
        for reg in training.registrations:
            display_name = reg.display_name or reg.username or 'Без имени'
            
            # Получаем статус team_assigned из таблицы TeamAssignment
            team_assignment = db_session.query(TeamAssignment)\
                .filter_by(training_id=training.id, user_id=reg.user_id)\
                .first()
            team_assigned = team_assignment.team_assigned if team_assignment else False
            
            if reg.goalkeeper:
                goalkeepers.append((display_name, reg.jersey_type, reg.paid))
            elif team_assigned and reg.jersey_type and reg.position_type:
                # Добавляем информацию об амплуа для полевых игроков
                position_info = ""
                if reg.position_type:
                    if reg.position_type.value == 'forward':
                        position_info = " - Нап"
                    else:
                        position_info = " - Зщ"
                
                if reg.jersey_type.value == 'light':
                    light_players.append((display_name, reg.paid, position_info))
                elif reg.jersey_type.value == 'dark':
                    dark_players.append((display_name, reg.paid, position_info))
                elif reg.jersey_type.value == 'blue':
                    blue_players.append((display_name, reg.paid, position_info))
                elif reg.jersey_type.value == 'yellow':
                    yellow_players.append((display_name, reg.paid, position_info))
            else:
                unassigned.append((display_name, reg.paid))
        
        # Выводим вратарей
        if goalkeepers:
            message += "🥅 *Вратари:*\n"
            for name, jersey_type, paid in goalkeepers:
                jersey_emoji = jersey_emojis.get(jersey_type.value, '👕') if jersey_type else '👕'
                message += f"• {escape_markdown(name)} {jersey_emoji}\n"
            message += "\n"
        
        # Выводим игроков по цветам маек
        if light_players:
            message += "⚪ *Белые:*\n"
            for name, paid, position_info in light_players:
                message += f"• {escape_markdown(name)}{position_info}\n"
            message += "\n"
        
        if dark_players:
            message += "⚫ *Черные:*\n"
            for name, paid, position_info in dark_players:
                message += f"• {escape_markdown(name)}{position_info}\n"
            message += "\n"
        
        if blue_players:
            message += "🔵 *Синие:*\n"
            for name, paid, position_info in blue_players:
                message += f"• {escape_markdown(name)}{position_info}\n"
            message += "\n"
        
        if yellow_players:
            message += "🟡 *Желтые:*\n"
            for name, paid, position_info in yellow_players:
                message += f"• {escape_markdown(name)}{position_info}\n"
            message += "\n"
        
        # Выводим нераспределенных участников
        if unassigned:
            message += "❓ *Нераспределенные:*\n"
            for name, paid in unassigned:
                message += f"• {escape_markdown(name)}\n"
            message += "\n"
        
        message += "━━━━━━━━━━━━━━━\n\n"
    
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
            
            # Получаем статус team_assigned из таблицы TeamAssignment
            team_assignment = db_session.query(TeamAssignment)\
                .filter_by(training_id=training.id, user_id=reg.user_id)\
                .first()
            team_assigned = team_assignment.team_assigned if team_assignment else False
            
            # Если команда назначена, показываем полную информацию
            if team_assigned:
                # Добавляем информацию о выбранной футболке
                jersey_emojis = {
                    'light': '⚪',
                    'dark': '⚫',
                    'blue': '🔵',
                    'yellow': '🟡'
                }
                if reg.jersey_type:
                    jersey_info = jersey_emojis.get(reg.jersey_type.value, '👕')
                    message += f"{i}. {display_name} {jersey_info}"
                else:
                    message += f"{i}. {display_name}"
                
                # Добавляем информацию об амплуа для полевых игроков
                if not reg.goalkeeper and reg.position_type:
                    if reg.position_type.value == 'forward':
                        position_info = " - Нап"
                    else:
                        position_info = " - Зщ"
                    message += f"{position_info}"
                
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

/test_weekly_post - Отправить тестовый еженедельный пост
"""
        commands += admin_commands
    
    reply_markup = get_standard_keyboard()
    await update.message.reply_text(commands, reply_markup=reply_markup)

@handle_telegram_errors
async def test_weekly_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для тестирования еженедельного поста (только для администраторов)"""
    user_id = update.effective_user.id
    
    if user_id not in Config.ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав для выполнения этой команды")
        return
    
    try:
        # Отправляем тестовый пост
        success = await send_weekly_training_post(context.bot)
        
        if success:
            await update.message.reply_text("✅ Тестовый еженедельный пост успешно отправлен!")
        else:
            await update.message.reply_text("❌ Не удалось отправить тестовый пост. Проверьте настройки CHANNEL_ID и права бота.")
            
    except Exception as e:
        logger.error(f"Ошибка при отправке тестового поста: {e}")
        await update.message.reply_text(f"❌ Ошибка при отправке тестового поста: {e}")

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
    application.add_handler(CommandHandler("test_weekly_post", test_weekly_post))
    application.add_handler(CallbackQueryHandler(register_training, pattern="^register_\d+$"))
    application.add_handler(CallbackQueryHandler(show_schedule, pattern="^schedule$"))
    application.add_handler(CallbackQueryHandler(show_my_registrations, pattern="^my_registrations$"))
    application.add_handler(CallbackQueryHandler(cancel_registration, pattern="^cancel_\d+$"))
    application.add_handler(CallbackQueryHandler(mark_payment, pattern="^pay_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_mark_payment, pattern="^mark_payment$"))
    application.add_handler(CallbackQueryHandler(handle_cancel_registration, pattern="^cancel_registration$"))
    application.add_handler(CallbackQueryHandler(view_training_participants, pattern="^view_participants$"))
    application.add_handler(CallbackQueryHandler(return_to_start, pattern="^start$"))
    
    # Настройки для polling с обработкой ошибок
    try:
        # Запускаем бота без блокировки
        await application.initialize()
        await application.start()
        
        # Настраиваем polling с параметрами для обработки сетевых ошибок
        try:
            await application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=['message', 'callback_query'],
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30
            )
            print("✅ Telegram бот успешно запущен")
        except Conflict as e:
            logger.warning(f"⚠️ Конфликт Telegram бота: {e}")
            logger.warning("⚠️ Возможно, запущен другой экземпляр бота. Бот будет работать в ограниченном режиме.")
            # Продолжаем работу без polling, но бот все еще может отправлять сообщения
            print("⚠️ Telegram бот запущен в ограниченном режиме (без polling)")
        
        # Запускаем планировщик запланированных сообщений
        from .message_scheduler import start_message_scheduler
        await start_message_scheduler(application.bot)
        
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

# Обработчики для новых кнопок
async def handle_mark_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Оплатил'"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Получаем все неоплаченные регистрации пользователя (исключая вратарей)
    unpaid_registrations = db_session.query(Registration)\
        .join(Training)\
        .filter(Registration.user_id == user_id)\
        .filter(Registration.paid == False)\
        .filter(Registration.goalkeeper == False)\
        .order_by(Training.date_time)\
        .all()
    
    if not unpaid_registrations:
        await query.answer("У вас нет неоплаченных записей")
        return
    
    # Отмечаем самую раннюю по дате неоплаченную тренировку
    earliest_registration = unpaid_registrations[0]
    earliest_registration.paid = True
    db_session.commit()
    
    training_date = earliest_registration.training.date_time.strftime('%d.%m.%Y %H:%M')
    await query.answer(f"✅ Оплата за {training_date} отмечена!")
    await show_my_registrations(update, context)

async def handle_cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Отменить запись'"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Получаем все активные регистрации пользователя
    active_registrations = db_session.query(Registration)\
        .join(Training)\
        .filter(Registration.user_id == user_id)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .all()
    
    if not active_registrations:
        await query.answer("У вас нет активных записей")
        return
    
    # Если только одна запись, отменяем её сразу
    if len(active_registrations) == 1:
        registration = active_registrations[0]
        db_session.delete(registration)
        db_session.commit()
        await query.answer("✅ Запись отменена!")
        await show_my_registrations(update, context)
        return
    
    # Если несколько записей, показываем список для выбора
    message = "❌ Выберите запись для отмены:\n\n"
    keyboard = []
    
    for i, reg in enumerate(active_registrations, 1):
        message += f"{i}. 📅 {reg.training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
        keyboard.append([InlineKeyboardButton(
            f"❌ Отменить {reg.training.date_time.strftime('%d.%m %H:%M')}",
            callback_data=f'cancel_{reg.id}'
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='my_registrations')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.answer()
    await query.message.reply_text(message, reply_markup=reply_markup)

# Функции для напоминаний об оплате
async def send_payment_reminder(registration: Registration, training: Training, bot):
    """Отправляет напоминание об оплате участнику"""
    try:
        # Пропускаем вратарей - им не нужны напоминания об оплате
        if registration.goalkeeper:
            logger.info(f"Пропускаем напоминание для вратаря {registration.user_id}")
            return False
        
        # Формируем сообщение
        training_date = training.date_time.strftime('%d.%m.%Y в %H:%M')
        display_name = registration.display_name or registration.username or 'Участник'
        
        message = f"💳 *Напоминание об оплате*\n\n"
        message += f"Привет, {escape_markdown(display_name)}!\n\n"
        message += f"📅 Тренировка: {training_date}\n"
        message += f"⏰ Прошло уже 1.5 часа с начала тренировки\n"
        message += f"💰 Пожалуйста, подтвердите оплату тренировки\n\n"
        message += f"Нажмите кнопку ниже, чтобы отметить оплату:"
        
        # Создаем клавиатуру с кнопкой оплаты
        keyboard = {
            'inline_keyboard': [
                [{'text': '✅ Оплатил тренировку', 'callback_data': f'pay_{registration.id}'}],
                [{'text': '📋 Мои записи', 'callback_data': 'my_registrations'}]
            ]
        }
        
        # Отправляем сообщение
        await bot.send_message(
            chat_id=registration.user_id,
            text=message,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard['inline_keyboard'])
        )
        
        # Обновляем время последнего напоминания
        registration.last_payment_reminder = datetime.now()
        db_session.commit()
        
        logger.info(f"✅ Напоминание об оплате отправлено участнику {display_name} (ID: {registration.user_id})")
        return True
        
    except Forbidden as e:
        logger.warning(f"⚠️ Пользователь {registration.user_id} ({display_name}) заблокировал бота. Напоминания не будут отправляться.")
        # Обновляем время, чтобы не пытаться отправить снова в ближайшее время
        registration.last_payment_reminder = datetime.now()
        db_session.commit()
        return False
    except BadRequest as e:
        error_msg = str(e)
        if "chat not found" in error_msg.lower():
            logger.warning(f"⚠️ Чат с пользователем {registration.user_id} ({display_name}) не найден. Возможно, пользователь никогда не запускал бота.")
            # Обновляем время, чтобы не пытаться отправить снова в ближайшее время
            registration.last_payment_reminder = datetime.now()
            db_session.commit()
        else:
            logger.error(f"❌ Некорректный запрос при отправке напоминания участнику {registration.user_id}: {e}")
        return False
    except (NetworkError, TimedOut) as e:
        logger.error(f"❌ Сетевая ошибка при отправке напоминания участнику {registration.user_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка отправки напоминания участнику {registration.user_id}: {e}")
        return False

async def check_payment_reminders(bot):
    """Проверяет и отправляет напоминания об оплате"""
    try:
        from datetime import timedelta
        
        current_time = datetime.now()
        # Ищем тренировки, которые начались более 1.5 часа назад
        reminder_time = current_time - timedelta(hours=1, minutes=30)
        
        # Находим тренировки, которые начались более 1.5 часа назад
        trainings_to_check = db_session.query(Training)\
            .filter(Training.date_time <= reminder_time)\
            .all()
        
        logger.info(f"🔍 Проверка напоминаний об оплате. Найдено тренировок: {len(trainings_to_check)}")
        
        # Логируем найденные тренировки
        for training in trainings_to_check:
            time_since_start = current_time - training.date_time
            logger.debug(f"   📅 Тренировка {training.id}: {training.date_time.strftime('%d.%m.%Y %H:%M')} (прошло: {time_since_start})")
        
        total_reminders_sent = 0
        total_blocked_users = 0
        
        for training in trainings_to_check:
            # Находим неоплативших участников (исключая вратарей)
            unpaid_registrations = db_session.query(Registration)\
                .filter(Registration.training_id == training.id)\
                .filter(Registration.paid == False)\
                .filter(Registration.goalkeeper == False)\
                .all()
            
            logger.debug(f"   👥 Неоплативших участников на тренировке {training.id}: {len(unpaid_registrations)}")
            
            for registration in unpaid_registrations:
                # Проверяем, нужно ли отправлять напоминание
                should_send_reminder = False
                
                if registration.last_payment_reminder is None:
                    # Первое напоминание
                    should_send_reminder = True
                    logger.info(f"      💳 Первое напоминание для участника {registration.user_id}")
                else:
                    # Проверяем, прошёл ли час с последнего напоминания
                    time_since_last_reminder = current_time - registration.last_payment_reminder
                    if time_since_last_reminder >= timedelta(hours=1):
                        should_send_reminder = True
                        logger.info(f"      ⏰ Повторное напоминание для участника {registration.user_id} (прошло: {time_since_last_reminder})")
                    else:
                        logger.debug(f"      ⏳ Слишком рано для повторного напоминания участнику {registration.user_id} (прошло: {time_since_last_reminder})")
                
                if should_send_reminder:
                    success = await send_payment_reminder(registration, training, bot)
                    if success:
                        total_reminders_sent += 1
        
        logger.info(f"📊 Итоги отправки напоминаний об оплате:")
        logger.info(f"✅ Отправлено напоминаний: {total_reminders_sent}")
        
        return total_reminders_sent
        
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке напоминаний об оплате: {e}")
        # Делаем rollback при любой ошибке
        try:
            db_session.rollback()
            db_session.remove()
        except Exception:
            pass
        return 0 
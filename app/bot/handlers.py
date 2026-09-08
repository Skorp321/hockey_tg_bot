from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, Application
from telegram.error import NetworkError, TimedOut, BadRequest, Forbidden, Conflict
from datetime import datetime
import logging
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from ..models import (
    Training, Registration, UserPreferences, Player, TeamAssignment,
    SeasonPass, PassOffer,
)
from ..config import Config
from ..database import session_scope
from ..roster import (
    MONTHS_RU, POSITION_LABELS, has_season_pass, pass_window, period_start_for,
)
from ..settings import as_int, get_settings
from .roster_message import schedule_roster_update
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

async def build_pass_button(session, user_id):
    """Кнопка «Абонемент», если окно открыто и абонемента ещё нет.

    Вне окна кнопка просто не рисуется — устаревать нечему. Само нажатие всё равно
    перепроверяется на сервере: кнопка могла остаться в старом сообщении.
    """
    try:
        settings = await get_settings(session)
        window = await pass_window(
            session, open_days_before=as_int(settings, 'pass.open_days_before')
        )
        if window is None:
            return None
        period, _first = window
        if await has_season_pass(session, user_id, period):
            return None
        month = MONTHS_RU[period.month - 1]
        return InlineKeyboardButton(
            f"🎫 Абонемент на {month}",
            callback_data=f"buy_pass_{period.year:04d}-{period.month:02d}",
        )
    except Exception as exc:
        # Кнопка — не повод ронять экран
        logger.error(f"Не удалось построить кнопку абонемента: {exc}")
        return None


async def update_temporary_user_id(session, real_user_id, username):
    """
    Обновляет временный user_id на реальный, когда пользователь впервые взаимодействует с ботом.
    Временные user_id - это отрицательные числа, созданные на основе hash от username.
    """
    if not username:
        return

    try:
        # Ищем игрока с таким же username и отрицательным (временным) user_id
        temp_player = (await session.execute(
            select(Player)
            .where(Player.username == username)
            .where(Player.user_id < 0)
        )).scalars().first()

        if temp_player:
            logger.info(f"Найден временный игрок с username={username}, обновляем user_id с {temp_player.user_id} на {real_user_id}")

            # Обновляем все регистрации этого игрока
            registrations = (await session.execute(
                select(Registration).filter_by(user_id=temp_player.user_id)
            )).scalars().all()

            for reg in registrations:
                reg.user_id = real_user_id
                logger.info(f"Обновлена регистрация {reg.id} на тренировку {reg.training_id}")

            # Обновляем предпочтения пользователя, если есть
            temp_prefs = (await session.execute(
                select(UserPreferences).filter_by(user_id=temp_player.user_id)
            )).scalars().first()

            if temp_prefs:
                # Проверяем, нет ли уже предпочтений с реальным user_id
                real_prefs = (await session.execute(
                    select(UserPreferences).filter_by(user_id=real_user_id)
                )).scalars().first()

                if not real_prefs:
                    temp_prefs.user_id = real_user_id
                else:
                    # Если есть, удаляем временные предпочтения
                    await session.delete(temp_prefs)

            # Распределение по командам раньше здесь не переносилось: строки оставались
            # на отрицательном user_id, и только что слитый игрок показывался как
            # «Команда не назначена».
            team_assignments = (await session.execute(
                select(TeamAssignment).filter_by(user_id=temp_player.user_id)
            )).scalars().all()
            existing_assignments = set((await session.execute(
                select(TeamAssignment.training_id).filter_by(user_id=real_user_id)
            )).scalars().all())
            for assignment in team_assignments:
                if assignment.training_id in existing_assignments:
                    await session.delete(assignment)
                else:
                    assignment.user_id = real_user_id

            # Абонементы и отметки о разосланных предложениях — иначе купленный
            # абонемент терялся бы при первом же /start.
            for model in (SeasonPass, PassOffer):
                rows = (await session.execute(
                    select(model).filter_by(user_id=temp_player.user_id)
                )).scalars().all()
                existing_periods = set((await session.execute(
                    select(model.period_start).filter_by(user_id=real_user_id)
                )).scalars().all())
                for row in rows:
                    if row.period_start in existing_periods:
                        await session.delete(row)
                    else:
                        row.user_id = real_user_id

            # Проверяем, нет ли уже игрока с реальным user_id
            real_player = (await session.execute(
                select(Player).filter_by(user_id=real_user_id)
            )).scalars().first()

            if not real_player:
                # Обновляем user_id временного игрока
                temp_player.user_id = real_user_id
            else:
                # Если есть, объединяем данные и удаляем временного игрока
                real_player.total_registrations += temp_player.total_registrations
                if temp_player.first_registration < real_player.first_registration:
                    real_player.first_registration = temp_player.first_registration
                # Членство в составе не должно теряться при слиянии
                real_player.is_roster_member = (
                    real_player.is_roster_member or temp_player.is_roster_member
                )
                await session.delete(temp_player)

            await session.commit()
            logger.info(f"Успешно обновлен user_id для игрока {username}")

    except Exception as e:
        logger.error(f"Ошибка при обновлении временного user_id: {e}")
        await session.rollback()

@handle_telegram_errors
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Обновляем временный user_id на реальный, если необходимо
    user_id = update.effective_user.id
    username = update.effective_user.username
    if username:
        async with session_scope() as session:
            await update_temporary_user_id(session, user_id, username)

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

    async with session_scope() as session:
        # Обновляем временный user_id на реальный, если необходимо
        if username:
            await update_temporary_user_id(session, user_id, username)

        # Извлекаем ID тренировки из callback_data (формат: register_123)
        training_id = int(query.data.split('_')[1])

        # Получаем выбранную тренировку
        training = (await session.execute(
            select(Training)
            .where(Training.id == training_id)
            .where(Training.date_time > datetime.now())
        )).scalars().first()

        if not training:
            await query.answer("Тренировка не найдена или уже прошла")
            return

        # Проверяем, не записан ли уже пользователь
        existing_reg = (await session.execute(
            select(Registration).filter_by(training_id=training.id, user_id=user_id)
        )).scalars().first()

        if existing_reg:
            await query.answer("Вы уже записаны на эту тренировку")
            return

        # Проверяем количество участников
        participants_count = (await session.execute(
            select(func.count())
            .select_from(Registration)
            .where(Registration.training_id == training.id)
        )).scalar_one()

        if participants_count >= training.max_participants:
            await query.answer("К сожалению, все места уже заняты")
            return

        # Получаем предпочтения пользователя
        user_prefs = (await session.execute(
            select(UserPreferences).filter_by(user_id=user_id)
        )).scalars().first()

        # Создаем новую запись с предпочтениями пользователя
        # Используем display_name из предпочтений, если есть, иначе username
        display_name = user_prefs.display_name if user_prefs and user_prefs.display_name else None
        username = update.effective_user.username or "Без имени"

        # Абонемент покрывает все тренировки месяца, поэтому оплата отмечается сразу
        covered_by_pass = await has_season_pass(
            session, user_id, period_start_for(training.date_time)
        )

        registration = Registration(
            training_id=training.id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            registered_at=datetime.now(),
            jersey_type=user_prefs.preferred_jersey_type if user_prefs else None,
            goalkeeper=user_prefs.goalkeeper if user_prefs else False,
            paid=covered_by_pass,
        )

        try:
            session.add(registration)

            # Обновляем или создаем запись в таблице players
            existing_player = (await session.execute(
                select(Player).filter_by(user_id=user_id)
            )).scalars().first()
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
                session.add(new_player)

            await session.commit()
            schedule_roster_update(training.id)
            await query.answer("Вы успешно записались на тренировку!")

            # Отправляем сообщение с подтверждением и деталями
            message = f"✅ Вы записаны на тренировку:\n"
            message += f"📅 {training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
            message += f"👥 Участников: {participants_count + 1}/{training.max_participants}"

            reply_markup = get_standard_keyboard()
            await query.message.reply_text(message, reply_markup=reply_markup)
        except Exception as e:
            await session.rollback()
            print(f"Error during registration: {e}")
            await query.answer("Произошла ошибка при записи. Попробуйте позже.")

@handle_telegram_errors
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    async with session_scope() as session:
        # Обновляем временный user_id на реальный, если необходимо
        user_id = update.effective_user.id
        username = update.effective_user.username
        if username:
            await update_temporary_user_id(session, user_id, username)

        # Получаем все предстоящие тренировки
        trainings = (await session.execute(
            select(Training)
            .options(selectinload(Training.registrations))
            .where(Training.date_time > datetime.now())
            .order_by(Training.date_time)
        )).scalars().all()

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

    async with session_scope() as session:
        # Обновляем временный user_id на реальный, если необходимо
        if username:
            await update_temporary_user_id(session, user_id, username)

        # Получаем предстоящие тренировки
        upcoming_registrations = (await session.execute(
            select(Registration)
            .join(Training)
            .options(selectinload(Registration.training))
            .where(Registration.user_id == user_id)
            .where(Training.date_time > datetime.now())
            .order_by(Training.date_time)
        )).scalars().all()

        # Получаем прошедшие неоплаченные тренировки (только для не-вратарей)
        past_unpaid_registrations = (await session.execute(
            select(Registration)
            .join(Training)
            .options(selectinload(Registration.training))
            .where(Registration.user_id == user_id)
            .where(Training.date_time <= datetime.now())
            .where(Registration.paid.is_(False))
            .where(Registration.goalkeeper.is_(False))
            .order_by(Training.date_time)
        )).scalars().all()

        # Объединяем списки
        registrations = list(upcoming_registrations) + list(past_unpaid_registrations)

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
            team_assignment = (await session.execute(
                select(TeamAssignment).filter_by(training_id=reg.training_id, user_id=reg.user_id)
            )).scalars().first()
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
                    message += f" - {POSITION_LABELS.get(reg.position_type, '—')}"

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

        pass_button = await build_pass_button(session, user_id)
        if pass_button:
            keyboard.append([pass_button])

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

    async with session_scope() as session:
        # Находим регистрацию
        registration = (await session.execute(
            select(Registration).filter_by(id=registration_id, user_id=user_id)
        )).scalars().first()

        if not registration:
            await query.answer("Регистрация не найдена")
            return

        # Проверяем, что пользователь не оплатил уже
        if registration.paid:
            await query.answer("Вы уже отметили оплату для этой тренировки")
            return

        # Отмечаем как оплаченную
        registration.paid = True
        training_id = registration.training_id
        await session.commit()
        schedule_roster_update(training_id)

        await query.answer("✅ Оплата отмечена!")

    # Сессия закрыта: show_my_registrations открывает свою собственную
    await show_my_registrations(update, context)

async def view_training_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    async with session_scope() as session:
        # Получаем все предстоящие тренировки
        trainings = (await session.execute(
            select(Training)
            .options(selectinload(Training.registrations))
            .where(Training.date_time > datetime.now())
            .order_by(Training.date_time)
        )).scalars().all()

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
                team_assignment = (await session.execute(
                    select(TeamAssignment).filter_by(training_id=training.id, user_id=reg.user_id)
                )).scalars().first()
                team_assigned = team_assignment.team_assigned if team_assignment else False

                if reg.goalkeeper:
                    goalkeepers.append((display_name, reg.jersey_type, reg.paid))
                elif team_assigned and reg.jersey_type and reg.position_type:
                    # Добавляем информацию об амплуа для полевых игроков
                    position_info = ""
                    if reg.position_type:
                        position_info = f" - {POSITION_LABELS.get(reg.position_type, '—')}"

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

    async with session_scope() as session:
        # Находим и удаляем регистрацию
        registration = (await session.execute(
            select(Registration).filter_by(id=reg_id, user_id=user_id)
        )).scalars().first()

        if registration:
            # Сохраняем display_name в UserPreferences перед удалением регистрации
            if registration.display_name:
                user_prefs = (await session.execute(
                    select(UserPreferences).filter_by(user_id=user_id)
                )).scalars().first()
                if not user_prefs:
                    user_prefs = UserPreferences(user_id=user_id)
                    session.add(user_prefs)
                user_prefs.display_name = registration.display_name

            cancelled_training_id = registration.training_id
            await session.delete(registration)
            await session.commit()
            schedule_roster_update(cancelled_training_id)
            await query.answer("Запись отменена")
            message = "Ваша запись успешно отменена"
            reply_markup = get_standard_keyboard()
            await query.message.reply_text(message, reply_markup=reply_markup)
        else:
            await query.answer("Запись не найдена")

async def view_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для просмотра участников ближайшей тренировки"""
    async with session_scope() as session:
        # Получаем ближайшую тренировку
        training = (await session.execute(
            select(Training)
            .where(Training.date_time > datetime.now())
            .order_by(Training.date_time)
        )).scalars().first()

        if not training:
            message = "Нет запланированных тренировок."
            reply_markup = get_standard_keyboard()
            await update.message.reply_text(message, reply_markup=reply_markup)
            return

        # Получаем список участников
        registrations = (await session.execute(
            select(Registration).filter_by(training_id=training.id)
        )).scalars().all()

        # Формируем сообщение
        message = f"📅 Тренировка {training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
        message += f"👥 Участники ({len(registrations)}/{training.max_participants}):\n\n"

        if registrations:
            for i, reg in enumerate(registrations, 1):
                # Используем display_name если есть, иначе username
                display_name = reg.display_name or reg.username or "Без имени"

                # Получаем статус team_assigned из таблицы TeamAssignment
                team_assignment = (await session.execute(
                    select(TeamAssignment).filter_by(training_id=training.id, user_id=reg.user_id)
                )).scalars().first()
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
                        message += f" - {POSITION_LABELS.get(reg.position_type, '—')}"

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
    application.add_handler(CallbackQueryHandler(register_training, pattern=r"^register_\d+$"))
    application.add_handler(CallbackQueryHandler(show_schedule, pattern="^schedule$"))
    application.add_handler(CallbackQueryHandler(show_my_registrations, pattern="^my_registrations$"))
    application.add_handler(CallbackQueryHandler(cancel_registration, pattern=r"^cancel_\d+$"))
    application.add_handler(CallbackQueryHandler(mark_payment, pattern=r"^pay_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_mark_payment, pattern="^mark_payment$"))
    application.add_handler(CallbackQueryHandler(handle_cancel_registration, pattern="^cancel_registration$"))
    application.add_handler(CallbackQueryHandler(view_training_participants, pattern="^view_participants$"))
    application.add_handler(CallbackQueryHandler(handle_buy_pass, pattern=r"^buy_pass_\d{4}-\d{2}$"))
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

    async with session_scope() as session:
        # Получаем все неоплаченные регистрации пользователя (исключая вратарей)
        unpaid_registrations = (await session.execute(
            select(Registration)
            .join(Training)
            .options(selectinload(Registration.training))
            .where(Registration.user_id == user_id)
            .where(Registration.paid.is_(False))
            .where(Registration.goalkeeper.is_(False))
            .order_by(Training.date_time)
        )).scalars().all()

        if not unpaid_registrations:
            await query.answer("У вас нет неоплаченных записей")
            return

        # Отмечаем самую раннюю по дате неоплаченную тренировку
        earliest_registration = unpaid_registrations[0]
        earliest_registration.paid = True
        paid_training_id = earliest_registration.training_id
        await session.commit()
        schedule_roster_update(paid_training_id)

        training_date = earliest_registration.training.date_time.strftime('%d.%m.%Y %H:%M')
        await query.answer(f"✅ Оплата за {training_date} отмечена!")

    # Сессия закрыта: show_my_registrations открывает свою собственную
    await show_my_registrations(update, context)

async def handle_cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Отменить запись'"""
    query = update.callback_query
    user_id = update.effective_user.id

    async with session_scope() as session:
        # Получаем все активные регистрации пользователя
        active_registrations = (await session.execute(
            select(Registration)
            .join(Training)
            .options(selectinload(Registration.training))
            .where(Registration.user_id == user_id)
            .where(Training.date_time > datetime.now())
            .order_by(Training.date_time)
        )).scalars().all()

        if not active_registrations:
            await query.answer("У вас нет активных записей")
            return

        # Если только одна запись, отменяем её сразу
        if len(active_registrations) == 1:
            registration = active_registrations[0]
            cancelled_training_id = registration.training_id
            await session.delete(registration)
            await session.commit()
            schedule_roster_update(cancelled_training_id)
            await query.answer("✅ Запись отменена!")
            single_cancelled = True
        else:
            single_cancelled = False
            # Если несколько записей, показываем список для выбора
            message = "❌ Выберите запись для отмены:\n\n"
            keyboard = []

            for i, reg in enumerate(active_registrations, 1):
                message += f"{i}. 📅 {reg.training.date_time.strftime('%d.%m.%Y %H:%M')}\n"
                keyboard.append([InlineKeyboardButton(
                    f"❌ Отменить {reg.training.date_time.strftime('%d.%m %H:%M')}",
                    callback_data=f'cancel_{reg.id}'
                )])

    # Сессия закрыта: show_my_registrations открывает свою собственную
    if single_cancelled:
        await show_my_registrations(update, context)
        return

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='my_registrations')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.answer()
    await query.message.reply_text(message, reply_markup=reply_markup)

async def handle_buy_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Покупка абонемента на месяц.

    Абонемент покрывает все тренировки календарного месяца, поэтому ₽ у его владельца
    проставляется автоматически — и на уже существующих записях, и на будущих.
    """
    query = update.callback_query
    user_id = update.effective_user.id

    try:
        period = datetime.strptime(query.data.replace('buy_pass_', ''), '%Y-%m').date()
    except ValueError:
        await query.answer("Некорректная кнопка")
        return

    async with session_scope() as session:
        settings = await get_settings(session)
        window = await pass_window(
            session, open_days_before=as_int(settings, 'pass.open_days_before')
        )
        # Проверяем окно на сервере: кнопка могла остаться в старом сообщении в чате.
        if window is None or window[0] != period:
            await query.answer("Покупка абонемента сейчас недоступна")
            return

        if await has_season_pass(session, user_id, period):
            await query.answer("Абонемент уже оформлен")
            return

        session.add(SeasonPass(user_id=user_id, period_start=period, created_by='bot'))
        try:
            await session.commit()
        except IntegrityError:
            # Дважды нажали подряд — второй раз натыкаемся на уникальный индекс
            await session.rollback()
            await query.answer("Абонемент уже оформлен")
            return

        # Задним числом закрываем оплату по уже существующим записям месяца
        month_start = datetime(period.year, period.month, 1)
        month_end = (datetime(period.year + 1, 1, 1) if period.month == 12
                     else datetime(period.year, period.month + 1, 1))
        registrations = (await session.execute(
            select(Registration)
            .join(Training)
            .options(selectinload(Registration.training))
            .where(Registration.user_id == user_id)
            .where(Training.date_time >= month_start)
            .where(Training.date_time < month_end)
        )).scalars().all()
        affected = []
        for registration in registrations:
            if not registration.paid:
                registration.paid = True
            affected.append(registration.training_id)
        await session.commit()

    for training_id in set(affected):
        schedule_roster_update(training_id)

    await query.answer("✅ Абонемент оформлен!")
    await query.message.reply_text(
        f"🎫 Абонемент на {MONTHS_RU[period.month - 1]} оформлен.\n"
        f"Оплата тренировок этого месяца отмечается автоматически.",
        reply_markup=get_standard_keyboard(),
    )


async def check_season_pass_offers(bot):
    """Рассылает предложение купить абонемент, когда открывается окно.

    Устроено так же, как check_payment_reminders: та же фоновая задача, та же защита
    от повторной отправки через отдельную таблицу-отметку.
    """
    try:
        async with session_scope() as session:
            settings = await get_settings(session)
            window = await pass_window(
                session, open_days_before=as_int(settings, 'pass.open_days_before')
            )
            if window is None:
                return 0
            period, first_training = window

            # Только состав и только реальные телеграм-аккаунты: у заведённых вручную
            # user_id отрицательный, и отправка им всё равно не пройдёт.
            players = (await session.execute(
                select(Player)
                .where(Player.is_roster_member.is_(True))
                .where(Player.user_id > 0)
            )).scalars().all()
            if not players:
                return 0

            user_ids = [p.user_id for p in players]
            already_paid = set((await session.execute(
                select(SeasonPass.user_id)
                .where(SeasonPass.period_start == period)
                .where(SeasonPass.user_id.in_(user_ids))
            )).scalars().all())
            already_offered = set((await session.execute(
                select(PassOffer.user_id)
                .where(PassOffer.period_start == period)
                .where(PassOffer.user_id.in_(user_ids))
            )).scalars().all())

            targets = [
                p for p in players
                if p.user_id not in already_paid and p.user_id not in already_offered
            ]

            sent = 0
            month = MONTHS_RU[period.month - 1]
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
                f"🎫 Оформить абонемент на {month}",
                callback_data=f"buy_pass_{period.year:04d}-{period.month:02d}",
            )]])

            for player in targets:
                # Отметку ставим ДО отправки: при сбое лучше не отправить, чем слать
                # одно и то же каждые полчаса.
                session.add(PassOffer(user_id=player.user_id, period_start=period))
                await session.commit()
                try:
                    await bot.send_message(
                        chat_id=player.user_id,
                        text=(
                            f"🎫 Открыт приём на абонемент — {month}\n\n"
                            f"Первая тренировка месяца: "
                            f"{first_training.strftime('%d.%m.%Y %H:%M')}\n"
                            f"Абонемент покрывает все тренировки месяца, "
                            f"оплата будет отмечаться автоматически."
                        ),
                        reply_markup=keyboard,
                    )
                    sent += 1
                except Forbidden:
                    logger.info(f"Игрок {player.user_id} заблокировал бота, предложение пропущено")
                except BadRequest as exc:
                    logger.info(f"Не удалось предложить абонемент {player.user_id}: {exc}")
                except (NetworkError, TimedOut) as exc:
                    logger.warning(f"Сеть недоступна при отправке предложения: {exc}")

            if sent:
                logger.info(f"🎫 Предложений абонемента отправлено: {sent}")
            return sent
    except Exception as exc:
        logger.error(f"❌ Ошибка при рассылке предложений абонемента: {exc}")
        return 0


# Функции для напоминаний об оплате
async def send_payment_reminder(session, registration: Registration, training: Training, bot):
    """Отправляет напоминание об оплате участнику"""
    display_name = registration.display_name or registration.username or 'Участник'
    try:
        # Пропускаем вратарей - им не нужны напоминания об оплате
        if registration.goalkeeper:
            logger.info(f"Пропускаем напоминание для вратаря {registration.user_id}")
            return False

        # Формируем сообщение
        training_date = training.date_time.strftime('%d.%m.%Y в %H:%M')

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
        await session.commit()

        logger.info(f"✅ Напоминание об оплате отправлено участнику {display_name} (ID: {registration.user_id})")
        return True

    except Forbidden as e:
        logger.warning(f"⚠️ Пользователь {registration.user_id} ({display_name}) заблокировал бота. Напоминания не будут отправляться.")
        # Обновляем время, чтобы не пытаться отправить снова в ближайшее время
        registration.last_payment_reminder = datetime.now()
        await session.commit()
        return False
    except BadRequest as e:
        error_msg = str(e)
        if "chat not found" in error_msg.lower():
            logger.warning(f"⚠️ Чат с пользователем {registration.user_id} ({display_name}) не найден. Возможно, пользователь никогда не запускал бота.")
            # Обновляем время, чтобы не пытаться отправить снова в ближайшее время
            registration.last_payment_reminder = datetime.now()
            await session.commit()
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

        async with session_scope() as session:
            # Находим тренировки, которые начались более 1.5 часа назад
            trainings_to_check = (await session.execute(
                select(Training).where(Training.date_time <= reminder_time)
            )).scalars().all()

            logger.info(f"🔍 Проверка напоминаний об оплате. Найдено тренировок: {len(trainings_to_check)}")

            # Логируем найденные тренировки
            for training in trainings_to_check:
                time_since_start = current_time - training.date_time
                logger.debug(f"   📅 Тренировка {training.id}: {training.date_time.strftime('%d.%m.%Y %H:%M')} (прошло: {time_since_start})")

            total_reminders_sent = 0

            for training in trainings_to_check:
                # Находим неоплативших участников (исключая вратарей)
                unpaid_registrations = (await session.execute(
                    select(Registration)
                    .where(Registration.training_id == training.id)
                    .where(Registration.paid.is_(False))
                    .where(Registration.goalkeeper.is_(False))
                )).scalars().all()

                # Владельцы абонемента уже оплатили месяц: без этого фильтра бот
                # напоминал бы им об оплате каждый час.
                if unpaid_registrations:
                    pass_holders = set((await session.execute(
                        select(SeasonPass.user_id)
                        .where(SeasonPass.period_start == period_start_for(training.date_time))
                        .where(SeasonPass.user_id.in_(
                            [r.user_id for r in unpaid_registrations]
                        ))
                    )).scalars().all())
                    if pass_holders:
                        unpaid_registrations = [
                            r for r in unpaid_registrations if r.user_id not in pass_holders
                        ]

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
                        success = await send_payment_reminder(session, registration, training, bot)
                        if success:
                            total_reminders_sent += 1

            logger.info(f"📊 Итоги отправки напоминаний об оплате:")
            logger.info(f"✅ Отправлено напоминаний: {total_reminders_sent}")

            return total_reminders_sent

    except Exception as e:
        logger.error(f"❌ Ошибка при проверке напоминаний об оплате: {e}")

from datetime import datetime, timedelta
from pathlib import Path
import logging

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    Training,
    Registration,
    JerseyType,
    PositionType,
    UserPreferences,
    Player,
    TeamAssignment,
    ScheduledMessage,
    RepeatType,
)
from ..database import get_db, session_scope
from ..roster import POSITION_LABELS
from ..config import Config

# Наборы допустимых значений выводятся из перечислений: при добавлении цвета или амплуа
# правку не придётся повторять в пяти местах.
JERSEY_VALUES = [item.value for item in JerseyType]
POSITION_VALUES = [item.value for item in PositionType]
from ..bot.weekly_posts import send_weekly_training_post
from .security import require_login, json_error

logger = logging.getLogger(__name__)

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))

TELEGRAM_SEND_URL = f'https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage'


def _channel_error_message(error_description: str) -> str:
    """Приводит ошибку Telegram к тому же тексту, что показывался раньше."""
    lowered = error_description.lower()
    if 'chat not found' in lowered:
        return (
            f'Канал не найден. Проверьте, что:\n'
            f'1. CHANNEL_ID указан правильно (должен начинаться с -100 для каналов/супергрупп)\n'
            f'2. Бот добавлен в канал/группу как администратор\n'
            f'3. Бот имеет права на отправку сообщений\n\n'
            f'Текущий CHANNEL_ID: {Config.CHANNEL_ID}'
        )
    if 'bot was blocked' in lowered:
        return 'Бот заблокирован в канале. Добавьте бота обратно в канал.'
    if 'not enough rights' in lowered:
        return (
            'У бота недостаточно прав. Убедитесь, что бот является администратором '
            'канала с правами на отправку сообщений.'
        )
    return f'Не удалось отправить сообщение: {error_description}'


@router.get('/login')
async def login_form(request: Request):
    return templates.TemplateResponse(request=request, name='login.html', context={})


@router.post('/login')
async def login(
    request: Request,
    username: str = Form(None),
    password: str = Form(None),
):
    if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
        request.session['logged_in'] = True
        return RedirectResponse('/', status_code=302)
    return templates.TemplateResponse(
        request=request,
        name='login.html',
        context={'error': "Неверные учетные данные"},
    )


@router.get('/logout')
async def logout(request: Request):
    request.session.pop('logged_in', None)
    return RedirectResponse('/login', status_code=302)


@router.get('/')
async def index(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    # Получаем будущие тренировки
    upcoming_trainings = (await session.execute(
        select(Training)
        .options(selectinload(Training.registrations))
        .where(Training.date_time > datetime.now())
        .order_by(Training.date_time)
    )).scalars().all()

    # Получаем прошедшие тренировки (за последние 30 дней)
    past_trainings = (await session.execute(
        select(Training)
        .options(selectinload(Training.registrations))
        .where(Training.date_time <= datetime.now())
        .where(Training.date_time >= datetime.now() - timedelta(days=7))
        .order_by(Training.date_time.desc())
    )).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name='schedule.html',
        context={
            'upcoming_trainings': upcoming_trainings,
            'past_trainings': past_trainings,
        },
    )


@router.post('/training')
async def add_training(
    date_time: str = Form(...),
    max_participants: str = Form(...),
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = Training(
            date_time=datetime.strptime(date_time, '%Y-%m-%dT%H:%M'),
            max_participants=int(max_participants),
        )
        session.add(training)
        await session.commit()

        return {'success': True}
    except Exception as e:
        logger.error(f"Error adding training: {e}")
        await session.rollback()
        return json_error(e, 400)


@router.delete('/training/{training_id}')
async def delete_training(
    training_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    training = (await session.execute(
        select(Training)
        .options(
            selectinload(Training.registrations),
            selectinload(Training.team_assignments),
        )
        .where(Training.id == training_id)
    )).scalars().first()
    if training:
        await session.delete(training)
        await session.commit()
    return {'success': True}


@router.get('/training/{training_id}/participants')
async def get_participants(
    training_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    training = (await session.execute(
        select(Training)
        .options(selectinload(Training.registrations))
        .where(Training.id == training_id)
    )).scalars().first()
    if not training:
        return JSONResponse(status_code=404, content={'error': 'Training not found'})

    participants = []
    for reg in training.registrations:
        # Используем display_name если есть, иначе username
        display_name = reg.display_name or reg.username or 'Без имени'

        # Получаем статус team_assigned из таблицы TeamAssignment
        team_assignment = (await session.execute(
            select(TeamAssignment).filter_by(training_id=training_id, user_id=reg.user_id)
        )).scalars().first()
        team_assigned = team_assignment.team_assigned if team_assignment else False

        # Сохранённые предпочтения игрока (для подстановки при отображении и при записи)
        user_prefs = (await session.execute(
            select(UserPreferences).filter_by(user_id=reg.user_id)
        )).scalars().first()
        preferred_jersey = user_prefs.preferred_jersey_type.value if user_prefs and user_prefs.preferred_jersey_type else None
        preferred_position = user_prefs.preferred_position_type.value if user_prefs and user_prefs.preferred_position_type else None

        participants.append({
            'id': reg.id,
            'user_id': reg.user_id,
            'username': reg.username or 'Без имени',
            'display_name': reg.display_name,
            'name': display_name,
            'registered_at': reg.registered_at.strftime('%d.%m.%Y %H:%M'),
            'jersey_type': reg.jersey_type.value if reg.jersey_type else None,
            'position_type': reg.position_type.value if reg.position_type else None,
            'preferred_jersey_type': preferred_jersey,
            'preferred_position_type': preferred_position,
            'goalkeeper': reg.goalkeeper,
            'team_assigned': team_assigned,
            'paid': reg.paid
        })

    return {
        'training_date': training.date_time.strftime('%d.%m.%Y %H:%M'),
        'participants': participants,
        'total': len(participants),
        'max': training.max_participants
    }


@router.post('/training/{training_id}/save-jerseys')
async def save_jerseys(
    request: Request,
    training_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = (await session.execute(
            select(Training)
            .options(selectinload(Training.registrations))
            .where(Training.id == training_id)
        )).scalars().first()
        if not training:
            return json_error('Training not found', 404)

        data = await request.json()
        participant_selections = data.get('participant_selections', {})

        if not participant_selections:
            return json_error('No participant selections provided', 400)

        # Сохраняем выбранные майки и команды в базу данных
        for registration in training.registrations:
            # Получаем отображаемое имя для поиска
            display_name = registration.display_name or registration.username
            if display_name in participant_selections:
                selection = participant_selections[display_name]

                # Сохраняем майку
                if 'jersey' in selection and selection['jersey'] in JERSEY_VALUES:
                    registration.jersey_type = JerseyType(selection['jersey'])

                # Сохраняем амплуа
                if 'position' in selection and selection['position'] in POSITION_VALUES:
                    registration.position_type = PositionType(selection['position'])

                # Устанавливаем флаг назначения команды в таблице TeamAssignment
                # Для вратарей: достаточно выбрать майку
                # Для полевых игроков: нужно выбрать и майку, и команду, и амплуа

                # Получаем или создаем запись в team_assignments
                team_assignment = (await session.execute(
                    select(TeamAssignment).filter_by(
                        training_id=training_id, user_id=registration.user_id
                    )
                )).scalars().first()

                if not team_assignment:
                    team_assignment = TeamAssignment(
                        training_id=training_id,
                        user_id=registration.user_id,
                        team_assigned=False,
                        assigned_at=None
                    )
                    session.add(team_assignment)

                if registration.goalkeeper:
                    if 'jersey' in selection and selection['jersey'] in JERSEY_VALUES:
                        logger.info(f"✅ Параметры сохранены для вратаря {display_name}")
                else:
                    has_jersey = 'jersey' in selection and selection['jersey'] in JERSEY_VALUES
                    has_position = 'position' in selection and selection['position'] in POSITION_VALUES

                    logger.info(f"🔍 Проверка для полевого игрока {display_name}: jersey={has_jersey}, position={has_position}")

                    if has_jersey and has_position:
                        logger.info(f"✅ Параметры сохранены для полевого игрока {display_name}")
                    else:
                        logger.warning(f"⚠️ НЕ все параметры выбраны для {display_name}")

        # Предпочтения пользователей будут обновлены в функции send_notifications
        # после успешной отправки уведомления

        await session.commit()

        return {'success': True, 'message': 'Майки и команды сохранены в базе данных'}

    except Exception as e:
        logger.error(f"Error saving jerseys: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.post('/training/{training_id}/notify')
async def send_notifications(
    request: Request,
    training_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = (await session.execute(
            select(Training)
            .options(selectinload(Training.registrations))
            .where(Training.id == training_id)
        )).scalars().first()
        if not training:
            return json_error('Training not found', 404)

        data = await request.json()
        changed_participants = data.get('changed_participants', [])

        training_date = training.date_time.strftime('%d.%m.%Y в %H:%M')
        success_count = 0
        failed_count = 0

        # Отправляем уведомления участникам:
        # 1. У которых team_assigned=False (еще не получили уведомления)
        # 2. У которых team_assigned=True, но изменились параметры
        logger.info(f"📋 Проверка уведомлений для тренировки {training_id}")
        logger.info(f"📋 Список изменившихся участников: {changed_participants}")

        # Один клиент на весь цикл: вызовы остаются последовательными, но
        # переиспользуют соединение и больше не блокируют event loop.
        async with httpx.AsyncClient(timeout=10) as client:
            for registration in training.registrations:
                display_name = registration.display_name or registration.username

                # Получаем статус распределения из таблицы team_assignments
                team_assignment = (await session.execute(
                    select(TeamAssignment).filter_by(
                        training_id=training_id, user_id=registration.user_id
                    )
                )).scalars().first()

                # Если записи в TeamAssignment нет, считаем игрока нераспределенным
                team_assigned = team_assignment.team_assigned if team_assignment else False

                # Дополнительная проверка: если записи нет, создаем её
                if not team_assignment:
                    logger.warning(f"⚠️ У участника {display_name} нет записи в TeamAssignment, создаем её")
                    team_assignment = TeamAssignment(
                        training_id=training_id,
                        user_id=registration.user_id,
                        team_assigned=False,
                        assigned_at=None
                    )
                    session.add(team_assignment)
                    await session.commit()
                    team_assigned = False

                # Проверяем, есть ли у игрока все необходимые параметры для распределения
                has_all_params = bool(
                    registration.jersey_type and (
                        registration.goalkeeper or
                        registration.position_type
                    )
                )

                # Получаем предпочтения пользователя из базы данных
                user_prefs = (await session.execute(
                    select(UserPreferences).filter_by(user_id=registration.user_id)
                )).scalars().first()

                # Проверяем, изменились ли параметры по сравнению с user_preferences
                params_changed = False
                if user_prefs:
                    # Проверяем изменения для вратарей и полевых игроков
                    if registration.goalkeeper:
                        # Для вратаря проверяем только майку
                        if registration.jersey_type != user_prefs.preferred_jersey_type:
                            params_changed = True
                            logger.info(f"🔄 Изменилась майка для вратаря {display_name}: {user_prefs.preferred_jersey_type} → {registration.jersey_type}")
                    else:
                        # Для полевого игрока проверяем майку и амплуа
                        if registration.jersey_type != user_prefs.preferred_jersey_type:
                            params_changed = True
                            logger.info(f"🔄 Изменилась майка для {display_name}: {user_prefs.preferred_jersey_type} → {registration.jersey_type}")
                        if registration.position_type != user_prefs.preferred_position_type:
                            params_changed = True
                            logger.info(f"🔄 Изменилось амплуа для {display_name}: {user_prefs.preferred_position_type} → {registration.position_type}")
                else:
                    # Если предпочтений нет, считаем что это новый игрок
                    params_changed = True
                    logger.info(f"🆕 Новый игрок {display_name}, предпочтения отсутствуют")

                # Отправляем уведомление если:
                # 1. У участника НЕТ статуса "Команда назначена" (team_assigned = False) ИЛИ
                # 2. Параметры изменились по сравнению с user_preferences
                should_notify = (
                    (not team_assigned and has_all_params) or
                    (params_changed and has_all_params)
                )

                logger.info(f"👤 Участник {display_name}: team_assigned={team_assigned}, has_all_params={has_all_params}, should_notify={should_notify}")
                logger.info(f"   📋 Параметры: jersey={registration.jersey_type}, position={registration.position_type}, goalkeeper={registration.goalkeeper}")

                # Для вратарей проверяем только майку, для полевых игроков - майку и амплуа
                if should_notify and registration.jersey_type and (
                        registration.goalkeeper or registration.position_type):

                    # Проверяем, есть ли у игрока user_id (может ли он получить уведомление через Telegram)
                    if not registration.user_id:
                        logger.info(f"⚠️ Игрок {display_name} добавлен вручную (без user_id), уведомление не отправляется")
                        # Обновляем статус team_assigned для игроков без user_id
                        if not team_assigned:
                            team_assignment.team_assigned = True
                            team_assignment.assigned_at = datetime.now()
                            logger.info(f"✅ Обновлен статус team_assigned=True для игрока без user_id {display_name}")
                        continue

                    # Формируем индивидуальное сообщение для участника
                    jersey_emojis = {
                        'light': '⚪',
                        'dark': '⚫',
                        'blue': '🔵',
                        'yellow': '🟡'
                    }
                    jersey_emoji = jersey_emojis.get(registration.jersey_type.value, '👕')

                    message = f"🏒 *Уведомление о тренировке*\n\n"
                    message += f"📅 Дата: {training_date}\n"
                    message += f"🎯 Ваша майка: {jersey_emoji}\n"

                    # Добавляем амплуа для полевых игроков
                    if not registration.goalkeeper and registration.position_type:
                        position_text = POSITION_LABELS.get(registration.position_type, "—")
                        message += f"🏒 Ваше амплуа: {position_text}\n"

                    message += f"👥 Всего участников: {len(training.registrations)}/{training.max_participants}"

                    try:
                        # Создаем клавиатуру с кнопками
                        keyboard = {
                            'inline_keyboard': [
                                [{'text': 'Показать расписание', 'callback_data': 'schedule'}],
                                [{'text': 'Мои записи', 'callback_data': 'my_registrations'}]
                            ]
                        }

                        # Отправляем сообщение через Telegram Bot API с кнопками
                        telegram_response = await client.post(
                            TELEGRAM_SEND_URL,
                            json={
                                'chat_id': registration.user_id,
                                'text': message,
                                'parse_mode': 'Markdown',
                                'reply_markup': keyboard
                            },
                        )

                        if telegram_response.status_code == 200:
                            success_count += 1
                            logger.info(f"✅ Уведомление отправлено участнику {display_name} ({registration.jersey_type.value})")

                            # Обновляем статус team_assigned после успешной отправки уведомления
                            if not team_assigned:
                                team_assignment.team_assigned = True
                                team_assignment.assigned_at = datetime.now()
                                logger.info(f"✅ Обновлен статус team_assigned=True для {display_name}")

                            # Обновляем user_preferences с новыми параметрами
                            if not user_prefs:
                                user_prefs = UserPreferences(user_id=registration.user_id)
                                session.add(user_prefs)
                                logger.info(f"🆕 Создаем новые предпочтения для {display_name}")

                            user_prefs.preferred_jersey_type = registration.jersey_type
                            if not registration.goalkeeper:
                                user_prefs.preferred_position_type = registration.position_type
                            logger.info(f"💾 Обновлены предпочтения для {display_name}")
                        else:
                            # Проверяем, является ли ошибка "chat not found" (игрок без Telegram аккаунта)
                            response_text = telegram_response.text
                            if "chat not found" in response_text.lower():
                                logger.info(f"ℹ️ Игрок {display_name} не имеет Telegram аккаунта, уведомление не отправлено")
                                # Обновляем статус team_assigned для игроков без Telegram аккаунта
                                if not team_assigned:
                                    team_assignment.team_assigned = True
                                    team_assignment.assigned_at = datetime.now()
                                    logger.info(f"✅ Обновлен статус team_assigned=True для игрока без Telegram аккаунта {display_name}")

                                # Обновляем user_preferences даже для игроков без Telegram аккаунта
                                if not user_prefs:
                                    user_prefs = UserPreferences(user_id=registration.user_id)
                                    session.add(user_prefs)

                                user_prefs.preferred_jersey_type = registration.jersey_type
                                if not registration.goalkeeper:
                                    user_prefs.preferred_position_type = registration.position_type
                                logger.info(f"💾 Обновлены предпочтения для игрока без Telegram аккаунта {display_name}")
                            else:
                                failed_count += 1
                                logger.error(f"❌ Ошибка отправки участнику {display_name}: {response_text}")

                    except Exception as e:
                        # Проверяем, является ли ошибка связанной с отсутствием Telegram аккаунта
                        error_str = str(e).lower()
                        if "chat not found" in error_str or "user not found" in error_str:
                            logger.info(f"ℹ️ Игрок {display_name} не имеет Telegram аккаунта, уведомление не отправлено")
                            # Обновляем статус team_assigned для игроков без Telegram аккаунта
                            if not team_assigned:
                                team_assignment.team_assigned = True
                                team_assignment.assigned_at = datetime.now()
                                logger.info(f"✅ Обновлен статус team_assigned=True для игрока без Telegram аккаунта {display_name}")

                            # Обновляем user_preferences даже для игроков без Telegram аккаунта
                            if not user_prefs:
                                user_prefs = UserPreferences(user_id=registration.user_id)
                                session.add(user_prefs)

                            user_prefs.preferred_jersey_type = registration.jersey_type
                            if not registration.goalkeeper:
                                user_prefs.preferred_position_type = registration.position_type
                            logger.info(f"💾 Обновлены предпочтения для игрока без Telegram аккаунта {display_name}")
                        else:
                            failed_count += 1
                            logger.error(f"❌ Ошибка отправки участнику {display_name}: {e}")

        # Логируем общий результат
        logger.info(f"📊 Итоги отправки уведомлений для тренировки {training_id}")
        logger.info(f"✅ Успешно отправлено: {success_count}")
        logger.info(f"❌ Ошибок отправки: {failed_count}")

        # Сохраняем изменения в базе данных
        await session.commit()

        if success_count > 0:
            return {
                'success': True,
                'message': f'Уведомления отправлены {success_count} участникам. Ошибок: {failed_count}'
            }
        elif failed_count > 0:
            return {
                'success': False,
                'error': f'Ошибки при отправке уведомлений: {failed_count}'
            }
        else:
            return {
                'success': True,
                'message': 'Все игроки распределены по командам!'
            }

    except Exception as e:
        logger.error(f"Error sending notifications: {e}")
        return json_error(e, 500)


@router.get('/training/{training_id}/quick-add-players')
async def get_quick_add_players(
    training_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = (await session.execute(
            select(Training)
            .options(selectinload(Training.registrations))
            .where(Training.id == training_id)
        )).scalars().first()
        if not training:
            return json_error('Training not found', 404)

        # Получаем ID участников, уже записанных на текущую тренировку
        current_participant_ids = [reg.user_id for reg in training.registrations]
        logger.info(f"Current participants on training {training_id}: {current_participant_ids}")

        # Получаем всех игроков из таблицы players
        all_players = (await session.execute(select(Player))).scalars().all()
        logger.info(f"Total players in database: {len(all_players)}")

        # Фильтруем игроков, которые не записаны на текущую тренировку
        available_players = []
        for player in all_players:
            if player.user_id not in current_participant_ids:
                # Проверяем предпочтения пользователя
                user_prefs = (await session.execute(
                    select(UserPreferences).filter_by(user_id=player.user_id)
                )).scalars().first()

                player_data = {
                    'user_id': player.user_id,
                    'username': player.username,
                    'display_name': player.display_name,
                    'goalkeeper': player.goalkeeper,
                    'last_registration': player.last_registration.strftime('%d.%m.%Y %H:%M'),
                    'total_registrations': player.total_registrations
                }

                # Обновляем данные из предпочтений, если есть
                if user_prefs:
                    if user_prefs.display_name:
                        player_data['display_name'] = user_prefs.display_name
                    player_data['goalkeeper'] = user_prefs.goalkeeper

                available_players.append(player_data)

        logger.info(f"Available players for quick add: {len(available_players)}")

        # Сортируем по последней дате регистрации (новые сверху)
        available_players.sort(key=lambda x: datetime.strptime(x['last_registration'], '%d.%m.%Y %H:%M'), reverse=True)

        return {
            'success': True,
            'players': available_players,
            'total': len(available_players),
            'debug': {
                'current_participants': current_participant_ids,
                'total_players': len(all_players),
                'available_players': len(available_players)
            }
        }

    except Exception as e:
        logger.error(f"Error getting quick add players: {e}")
        import traceback
        traceback.print_exc()
        return json_error(e, 500)


@router.post('/training/{training_id}/bulk-register')
async def bulk_register_players(
    request: Request,
    training_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = (await session.execute(
            select(Training)
            .options(selectinload(Training.registrations))
            .where(Training.id == training_id)
        )).scalars().first()
        if not training:
            return json_error('Training not found', 404)

        data = await request.json()
        players = data.get('players', [])

        if not players:
            return json_error('No players provided', 400)

        # Проверяем лимит участников
        current_count = len(training.registrations)
        if current_count + len(players) > training.max_participants:
            return json_error(
                f'Превышен лимит участников. Доступно мест: {training.max_participants - current_count}',
                400,
            )

        # Проверяем лимит вратарей
        current_goalkeepers = sum(1 for reg in training.registrations if reg.goalkeeper)
        new_goalkeepers = sum(1 for player in players if player.get('goalkeeper', False))
        if current_goalkeepers + new_goalkeepers > 2:
            return json_error('Максимум 2 вратаря на тренировку', 400)

        # Добавляем игроков
        added_count = 0
        for player in players:
            # asyncpg строже psycopg2 к типам: user_id из клиентского JSON может прийти строкой
            raw_user_id = player.get('user_id')
            user_id = int(raw_user_id) if raw_user_id is not None else None

            # Проверяем, не записан ли уже этот игрок
            existing_reg = (await session.execute(
                select(Registration).filter_by(training_id=training_id, user_id=user_id)
            )).scalars().first()

            if not existing_reg:
                # Получаем предпочтения пользователя
                user_prefs = (await session.execute(
                    select(UserPreferences).filter_by(user_id=user_id)
                )).scalars().first()

                # Создаем новую регистрацию
                registration = Registration(
                    training_id=training_id,
                    user_id=user_id,
                    username=player.get('username', ''),
                    display_name=player.get('display_name') or player.get('username', ''),
                    goalkeeper=player.get('goalkeeper', False),
                    registered_at=datetime.now()
                )

                # Применяем предпочтения пользователя
                if user_prefs:
                    registration.jersey_type = user_prefs.preferred_jersey_type
                    registration.position_type = user_prefs.preferred_position_type

                session.add(registration)

                # Обновляем или создаем запись в таблице players
                existing_player = (await session.execute(
                    select(Player).filter_by(user_id=user_id)
                )).scalars().first()
                if existing_player:
                    # Обновляем существующего игрока
                    existing_player.last_registration = datetime.now()
                    existing_player.total_registrations += 1
                    if player.get('display_name'):
                        existing_player.display_name = player.get('display_name')
                    existing_player.goalkeeper = player.get('goalkeeper', False)
                else:
                    # Создаем нового игрока
                    new_player = Player(
                        user_id=user_id,
                        username=player.get('username', ''),
                        display_name=player.get('display_name') or player.get('username', ''),
                        goalkeeper=player.get('goalkeeper', False),
                        first_registration=datetime.now(),
                        last_registration=datetime.now(),
                        total_registrations=1
                    )
                    session.add(new_player)

                added_count += 1

        await session.commit()

        return {
            'success': True,
            'message': f'Успешно добавлено {added_count} игроков',
            'added_count': added_count
        }

    except Exception as e:
        logger.error(f"Error bulk registering players: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.post('/search-telegram-user')
async def search_telegram_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Ищет пользователя в Telegram по username"""
    try:
        data = await request.json()
        username = data.get('username', '').strip().replace('@', '')

        if not username:
            return json_error('Username не указан', 400)

        # Сначала проверяем, есть ли пользователь в нашей базе
        existing_player = (await session.execute(
            select(Player).filter_by(username=username)
        )).scalars().first()
        if existing_player:
            return {
                'success': True,
                'user': {
                    'user_id': existing_player.user_id,
                    'username': existing_player.username,
                    'display_name': existing_player.display_name,
                    'first_name': existing_player.display_name or existing_player.username,
                    'goalkeeper': existing_player.goalkeeper
                },
                'found_in_db': True
            }

        # Если пользователя нет в базе, возвращаем возможность добавить его вручную
        # Для этого создаем временный user_id на основе username
        # При добавлении реального игрока через бота, этот user_id будет обновлен
        return {
            'success': True,
            'user': {
                'user_id': None,  # Будет заполнен при первой регистрации через бота
                'username': username,
                'display_name': None,
                'first_name': username,
                'goalkeeper': False
            },
            'found_in_db': False,
            'warning': 'Пользователь не найден в базе. Вы можете добавить его, но для получения уведомлений он должен будет написать боту.'
        }

    except Exception as e:
        logger.error(f"Error searching telegram user: {e}")
        import traceback
        traceback.print_exc()
        return json_error(e, 500)


@router.post('/add-player-by-username')
async def add_player_by_username(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Добавляет игрока по username или display_name напрямую в базу"""
    try:
        data = await request.json()
        username = data.get('username', '').strip().replace('@', '')
        display_name = data.get('display_name', '').strip()
        goalkeeper = data.get('goalkeeper', False)

        # Проверяем, что указан хотя бы один из параметров
        if not username and not display_name:
            return json_error('Необходимо указать либо Telegram логин, либо имя игрока', 400)

        # Если есть username, проверяем, есть ли уже такой пользователь в базе
        if username:
            existing_player = (await session.execute(
                select(Player).filter_by(username=username)
            )).scalars().first()
            if existing_player:
                return {
                    'success': True,
                    'user': {
                        'user_id': existing_player.user_id,
                        'username': existing_player.username,
                        'display_name': existing_player.display_name,
                        'goalkeeper': existing_player.goalkeeper
                    },
                    'message': 'Пользователь уже есть в базе'
                }

        # Создаем нового игрока с временным user_id
        # Используем отрицательный hash от username (если есть) или display_name как временный user_id
        identifier = username if username else display_name
        temp_user_id = -abs(hash(identifier + str(datetime.now().timestamp())) % (10 ** 10))

        # Проверяем, что такой user_id еще не существует (маловероятно, но на всякий случай)
        while (await session.execute(
            select(Player).filter_by(user_id=temp_user_id)
        )).scalars().first():
            temp_user_id = -abs(hash(identifier + str(datetime.now().timestamp()) + str(temp_user_id)) % (10 ** 10))

        new_player = Player(
            user_id=temp_user_id,
            username=username if username else None,
            display_name=display_name if display_name else username,
            goalkeeper=goalkeeper,
            first_registration=datetime.now(),
            last_registration=datetime.now(),
            total_registrations=0
        )

        session.add(new_player)
        await session.commit()

        return {
            'success': True,
            'user': {
                'user_id': temp_user_id,
                'username': username if username else None,
                'display_name': display_name if display_name else username,
                'goalkeeper': goalkeeper
            },
            'message': 'Игрок добавлен в базу'
        }

    except Exception as e:
        logger.error(f"Error adding player by username: {e}")
        import traceback
        traceback.print_exc()
        await session.rollback()
        return json_error(e, 500)


@router.delete('/training/{training_id}/participant/{participant_id}')
async def remove_participant(
    training_id: int,
    participant_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = await session.get(Training, training_id)
        if not training:
            return json_error('Training not found', 404)

        registration = (await session.execute(
            select(Registration).filter_by(id=participant_id, training_id=training_id)
        )).scalars().first()

        if not registration:
            return json_error('Participant not found', 404)

        participant_name = registration.display_name or registration.username or 'Без имени'

        # Удаляем запись из team_assignments
        team_assignment = (await session.execute(
            select(TeamAssignment).filter_by(
                training_id=training_id, user_id=registration.user_id
            )
        )).scalars().first()

        if team_assignment:
            await session.delete(team_assignment)
            logger.info(f"🗑️ Удалена запись TeamAssignment для участника {participant_name}")

        # Удаляем регистрацию
        await session.delete(registration)
        await session.commit()

        return {
            'success': True,
            'message': f'Участник {participant_name} удален из тренировки'
        }

    except Exception as e:
        logger.error(f"Error removing participant: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.post('/training/{training_id}/participant/{participant_id}/rename')
async def rename_participant(
    request: Request,
    training_id: int,
    participant_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = await session.get(Training, training_id)
        if not training:
            return json_error('Training not found', 404)

        registration = (await session.execute(
            select(Registration).filter_by(id=participant_id, training_id=training_id)
        )).scalars().first()

        if not registration:
            return json_error('Participant not found', 404)

        data = await request.json()
        new_name_input = data.get('name', '').strip()
        is_goalkeeper = data.get('goalkeeper', False)

        # Если новое имя пустое, используем текущее отображаемое имя
        new_name = new_name_input or registration.display_name or registration.username or 'Без имени'

        # Проверяем лимит вратарей (максимум 2)
        if is_goalkeeper:
            current_goalkeepers = (await session.execute(
                select(func.count())
                .select_from(Registration)
                .where(
                    Registration.training_id == training_id,
                    Registration.goalkeeper.is_(True),
                    Registration.id != participant_id,
                )
            )).scalar_one()
            if current_goalkeepers >= 2:
                return json_error('Максимум 2 вратаря на тренировку', 400)

        # Обновляем отображаемое имя и статус вратаря в регистрации
        registration.display_name = new_name
        registration.goalkeeper = is_goalkeeper

        # Обновляем отображаемое имя и статус вратаря в предпочтениях пользователя для будущих записей
        user_prefs = (await session.execute(
            select(UserPreferences).filter_by(user_id=registration.user_id)
        )).scalars().first()
        if not user_prefs:
            user_prefs = UserPreferences(user_id=registration.user_id)
            session.add(user_prefs)
        user_prefs.display_name = new_name
        user_prefs.goalkeeper = is_goalkeeper

        await session.commit()

        return {
            'success': True,
            'message': f'Имя участника изменено на "{new_name}"',
            'new_name': new_name
        }

    except Exception as e:
        logger.error(f"Error renaming participant: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.post('/training/{training_id}/participant/{participant_id}/remember-preferences')
async def remember_participant_preferences(
    request: Request,
    training_id: int,
    participant_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Сохраняет выбранные параметры игрока (майка, амплуа) в предпочтения для последующих записей."""
    try:
        training = await session.get(Training, training_id)
        if not training:
            return json_error('Training not found', 404)

        registration = (await session.execute(
            select(Registration).filter_by(id=participant_id, training_id=training_id)
        )).scalars().first()

        if not registration:
            return json_error('Participant not found', 404)

        try:
            data = await request.json()
        except Exception:
            data = None
        data = data or {}
        jersey_type = data.get('jersey_type')
        position_type = data.get('position_type')

        if jersey_type is not None and jersey_type not in JERSEY_VALUES:
            return json_error('Invalid jersey_type', 400)
        if position_type is not None and position_type not in POSITION_VALUES and not registration.goalkeeper:
            return json_error('Invalid position_type', 400)

        user_prefs = (await session.execute(
            select(UserPreferences).filter_by(user_id=registration.user_id)
        )).scalars().first()
        if not user_prefs:
            user_prefs = UserPreferences(user_id=registration.user_id)
            session.add(user_prefs)

        if jersey_type is not None:
            user_prefs.preferred_jersey_type = JerseyType(jersey_type)
        if not registration.goalkeeper and position_type is not None:
            user_prefs.preferred_position_type = PositionType(position_type)

        await session.commit()

        participant_name = registration.display_name or registration.username or 'Без имени'
        return {
            'success': True,
            'message': f'Предпочтения для {participant_name} сохранены'
        }
    except Exception as e:
        logger.error(f"Error saving participant preferences: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.post('/training/{training_id}/participant/{participant_id}/mark-paid')
async def mark_participant_paid(
    training_id: int,
    participant_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    try:
        training = await session.get(Training, training_id)
        if not training:
            return json_error('Training not found', 404)

        registration = (await session.execute(
            select(Registration).filter_by(id=participant_id, training_id=training_id)
        )).scalars().first()

        if not registration:
            return json_error('Participant not found', 404)

        # Проверяем, что это не вратарь
        if registration.goalkeeper:
            return json_error('Goalkeeper payment is not tracked', 400)

        # Устанавливаем флаг оплаты
        registration.paid = True

        await session.commit()

        participant_name = registration.display_name or registration.username or 'Без имени'

        return {
            'success': True,
            'message': f'Статус оплаты обновлен для {participant_name}'
        }

    except Exception as e:
        logger.error(f"Error marking participant as paid: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.get('/health')
async def health_check():
    """Health check endpoint для Docker"""
    # Сессия открывается внутри try, чтобы недоступная БД давала 503, а не 500:
    # на этот код завязаны healthcheck в docker-compose и cron-workflow.
    try:
        async with session_scope() as session:
            await session.execute(text('SELECT 1'))

        # Схема отстала от кода — считаем это нездоровьем, чтобы деплой упал сразу
        # на health-check, а не через часы на первой записи нового значения enum.
        from ..database import schema_stale_reason
        if schema_stale_reason:
            return JSONResponse(status_code=503, content={
                'status': 'unhealthy',
                'database': 'connected',
                'error': f'Схема БД отстала от кода: {schema_stale_reason}. '
                         f'Примените миграции: bash scripts/run-migrations.sh',
                'timestamp': datetime.now().isoformat()
            })

        return {
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        })


@router.post('/send-weekly-post')
async def send_weekly_post(
    request: Request,
    _: bool = Depends(require_login),
):
    """Отправляет еженедельный пост о тренировке"""
    # Используем уже запущенный экземпляр бота: раньше здесь создавалось
    # второе telegram.ext.Application и новый event loop прямо внутри запроса.
    try:
        bot = getattr(request.app.state, 'bot', None)
        if bot is None:
            return json_error('Бот не запущен. Проверьте логи запуска.', 500)

        success = await send_weekly_training_post(bot)

        if success:
            return {
                'success': True,
                'message': 'Еженедельный пост успешно отправлен!'
            }
        return json_error(
            'Не удалось отправить пост. Проверьте настройки CHANNEL_ID и права бота.',
            500,
        )

    except Exception as e:
        logger.error(f"Ошибка в send_weekly_post: {e}")
        return json_error(f'Ошибка: {str(e)}', 500)


@router.get('/messages')
async def messages_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Страница управления сообщениями"""
    messages = (await session.execute(
        select(ScheduledMessage).order_by(ScheduledMessage.created_at.desc())
    )).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name='messages.html',
        context={'messages': messages},
    )


@router.post('/messages')
async def create_message(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Создание нового сообщения"""
    try:
        data = await request.json()
        message_text = data.get('message_text', '').strip()

        if not message_text:
            return json_error('Текст сообщения не может быть пустым', 400)

        send_immediately = data.get('send_immediately', False)
        scheduled_time = None
        repeat_days = None

        # Получаем тип повторения
        repeat_type_str = data.get('repeat_type', 'once')
        repeat_type = RepeatType(repeat_type_str)

        # Получаем дни недели для еженедельного повторения
        if repeat_type == RepeatType.WEEKLY:
            days = data.get('repeat_days', [])
            if days:
                repeat_days = days

        # Если это запланированная отправка (не немедленная)
        if not send_immediately:
            scheduled_time_str = data.get('scheduled_time')
            if scheduled_time_str:
                scheduled_time = datetime.strptime(scheduled_time_str, '%Y-%m-%dT%H:%M')

        # Если это периодическое сообщение с немедленной отправкой,
        # устанавливаем scheduled_time для следующих отправок
        elif repeat_type != RepeatType.ONCE:
            scheduled_time_str = data.get('scheduled_time')
            if scheduled_time_str:
                # Используем указанное время для следующих отправок
                scheduled_time = datetime.strptime(scheduled_time_str, '%Y-%m-%dT%H:%M')
            else:
                # Если время не указано, устанавливаем на завтра в текущее время
                now = datetime.now()
                scheduled_time = (now + timedelta(days=1)).replace(second=0, microsecond=0)

        message = ScheduledMessage(
            message_text=message_text,
            send_immediately=send_immediately,
            scheduled_time=scheduled_time,
            repeat_type=repeat_type,
            is_active=True
        )

        if repeat_days:
            message.set_repeat_days(repeat_days)

        session.add(message)
        await session.commit()

        # Если нужно отправить немедленно
        if send_immediately:
            try:
                if not Config.CHANNEL_ID:
                    return json_error(
                        'CHANNEL_ID не настроен. Сообщение создано, но не отправлено.',
                        400,
                    )

                # Валидация формата CHANNEL_ID
                try:
                    channel_id_int = int(Config.CHANNEL_ID)
                    # Для каналов и супергрупп ID должен начинаться с -100
                    if channel_id_int > 0:
                        logger.warning(f"⚠️ CHANNEL_ID ({Config.CHANNEL_ID}) выглядит как личный чат. Для каналов/групп ID должен начинаться с -100")
                except (ValueError, TypeError):
                    return json_error(
                        f'CHANNEL_ID имеет неверный формат: {Config.CHANNEL_ID}. Должно быть числовое значение.',
                        400,
                    )

                # Отправляем сообщение через Telegram Bot API
                send_params = {
                    'chat_id': Config.CHANNEL_ID,
                    'text': message.message_text
                }

                # Добавляем message_thread_id только если он задан
                if Config.MESSAGE_THREAD_ID:
                    send_params['message_thread_id'] = int(Config.MESSAGE_THREAD_ID)

                async with httpx.AsyncClient(timeout=10) as client:
                    telegram_response = await client.post(TELEGRAM_SEND_URL, json=send_params)

                # Проверяем ответ от Telegram API
                response_data = telegram_response.json() if telegram_response.headers.get('content-type', '').startswith('application/json') else {}

                if telegram_response.status_code == 200 and response_data.get('ok', False):
                    # Обновляем время последней отправки
                    message.last_sent_at = datetime.now()
                    await session.commit()
                    logger.info(f"✅ Сообщение #{message.id} отправлено немедленно в канал {Config.CHANNEL_ID}")
                else:
                    # Извлекаем описание ошибки из ответа
                    error_description = response_data.get('description', telegram_response.text)
                    error_code = response_data.get('error_code', 'unknown')
                    logger.error(f"❌ Ошибка отправки сообщения (код {error_code}): {error_description}")

                    return json_error(_channel_error_message(error_description), 500)

            except Exception as e:
                logger.error(f"Ошибка при немедленной отправке: {e}")
                return json_error(f'Ошибка при отправке: {str(e)}', 500)

        return {
            'success': True,
            'message': 'Сообщение успешно создано'
        }

    except Exception as e:
        logger.error(f"Ошибка при создании сообщения: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.get('/messages/{message_id}')
async def get_message(
    message_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Получение сообщения по ID"""
    try:
        message = await session.get(ScheduledMessage, message_id)
        if not message:
            return json_error('Сообщение не найдено', 404)

        return {
            'success': True,
            'message': {
                'id': message.id,
                'message_text': message.message_text,
                'send_immediately': message.send_immediately,
                'scheduled_time': message.scheduled_time.isoformat() if message.scheduled_time else None,
                'repeat_type': message.repeat_type.value,
                'repeat_days': message.get_repeat_days(),
                'is_active': message.is_active,
                'last_sent_at': message.last_sent_at.isoformat() if message.last_sent_at else None
            }
        }
    except Exception as e:
        logger.error(f"Ошибка при получении сообщения: {e}")
        return json_error(e, 500)


@router.put('/messages/{message_id}')
async def update_message(
    request: Request,
    message_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Обновление сообщения"""
    try:
        message = await session.get(ScheduledMessage, message_id)
        if not message:
            return json_error('Сообщение не найдено', 404)

        data = await request.json()
        message_text = data.get('message_text', '').strip()

        if not message_text:
            return json_error('Текст сообщения не может быть пустым', 400)

        message.message_text = message_text
        send_immediately = data.get('send_immediately', False)
        message.send_immediately = send_immediately

        if not send_immediately:
            scheduled_time_str = data.get('scheduled_time')
            if scheduled_time_str:
                message.scheduled_time = datetime.strptime(scheduled_time_str, '%Y-%m-%dT%H:%M')
            else:
                message.scheduled_time = None

            repeat_type_str = data.get('repeat_type', 'once')
            message.repeat_type = RepeatType(repeat_type_str)

            if message.repeat_type == RepeatType.WEEKLY:
                days = data.get('repeat_days', [])
                message.set_repeat_days(days if days else None)
            else:
                message.set_repeat_days(None)

        await session.commit()

        return {
            'success': True,
            'message': 'Сообщение успешно обновлено'
        }

    except Exception as e:
        logger.error(f"Ошибка при обновлении сообщения: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.delete('/messages/{message_id}')
async def delete_message(
    message_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Удаление сообщения"""
    try:
        message = await session.get(ScheduledMessage, message_id)
        if not message:
            return json_error('Сообщение не найдено', 404)

        await session.delete(message)
        await session.commit()

        return {
            'success': True,
            'message': 'Сообщение успешно удалено'
        }
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.post('/messages/{message_id}/toggle')
async def toggle_message(
    message_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Активация/деактивация сообщения"""
    try:
        message = await session.get(ScheduledMessage, message_id)
        if not message:
            return json_error('Сообщение не найдено', 404)

        message.is_active = not message.is_active
        await session.commit()

        return {
            'success': True,
            'message': 'Статус сообщения изменен',
            'is_active': message.is_active
        }
    except Exception as e:
        logger.error(f"Ошибка при изменении статуса сообщения: {e}")
        await session.rollback()
        return json_error(e, 500)


@router.post('/messages/{message_id}/send-now')
async def send_message_now(
    message_id: int,
    session: AsyncSession = Depends(get_db),
    _: bool = Depends(require_login),
):
    """Немедленная отправка сообщения"""
    try:
        message = await session.get(ScheduledMessage, message_id)
        if not message:
            return json_error('Сообщение не найдено', 404)

        if not Config.CHANNEL_ID:
            return json_error('CHANNEL_ID не настроен. Проверьте настройки.', 400)

        # Валидация формата CHANNEL_ID
        try:
            channel_id_int = int(Config.CHANNEL_ID)
            # Для каналов и супергрупп ID должен начинаться с -100
            if channel_id_int > 0:
                logger.warning(f"⚠️ CHANNEL_ID ({Config.CHANNEL_ID}) выглядит как личный чат. Для каналов/групп ID должен начинаться с -100")
        except (ValueError, TypeError):
            return json_error(
                f'CHANNEL_ID имеет неверный формат: {Config.CHANNEL_ID}. Должно быть числовое значение.',
                400,
            )

        # Отправляем сообщение через Telegram Bot API
        send_params = {
            'chat_id': Config.CHANNEL_ID,
            'text': message.message_text
        }

        # Добавляем message_thread_id только если он задан
        if Config.MESSAGE_THREAD_ID:
            send_params['message_thread_id'] = int(Config.MESSAGE_THREAD_ID)

        async with httpx.AsyncClient(timeout=10) as client:
            telegram_response = await client.post(TELEGRAM_SEND_URL, json=send_params)

        # Проверяем ответ от Telegram API
        response_data = telegram_response.json() if telegram_response.headers.get('content-type', '').startswith('application/json') else {}

        if telegram_response.status_code == 200 and response_data.get('ok', False):
            # Обновляем время последней отправки
            message.last_sent_at = datetime.now()
            await session.commit()
            logger.info(f"✅ Сообщение #{message.id} отправлено немедленно в канал {Config.CHANNEL_ID}")
            return {
                'success': True,
                'message': 'Сообщение успешно отправлено'
            }
        else:
            # Извлекаем описание ошибки из ответа
            error_description = response_data.get('description', telegram_response.text)
            error_code = response_data.get('error_code', 'unknown')
            logger.error(f"❌ Ошибка отправки сообщения (код {error_code}): {error_description}")

            return json_error(_channel_error_message(error_description), 500)

    except Exception as e:
        logger.error(f"Ошибка в send_message_now: {e}")
        await session.rollback()
        return json_error(f'Ошибка: {str(e)}', 500)

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime, timedelta
from functools import wraps
import requests
import logging
from ..models import Training, Registration, JerseyType, PositionType, UserPreferences, Player, TeamAssignment, ScheduledMessage, RepeatType
from ..database import db_session
from ..config import Config
from ..bot.weekly_posts import send_weekly_training_post

logger = logging.getLogger(__name__)

web = Blueprint('web', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('web.login'))
        return f(*args, **kwargs)
    return decorated_function

@web.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('web.index'))
        return render_template('login.html', error="Неверные учетные данные")
    return render_template('login.html')

@web.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('web.login'))

@web.route('/')
@login_required
def index():
    # Получаем будущие тренировки
    upcoming_trainings = db_session.query(Training)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .all()
    
    # Получаем прошедшие тренировки (за последние 30 дней)
    past_trainings = db_session.query(Training)\
        .filter(Training.date_time <= datetime.now())\
        .filter(Training.date_time >= datetime.now() - timedelta(days=7))\
        .order_by(Training.date_time.desc())\
        .all()
    
    return render_template('schedule.html', 
                         upcoming_trainings=upcoming_trainings, 
                         past_trainings=past_trainings)

@web.route('/training', methods=['POST'])
@login_required
def add_training():
    try:
        data = request.form
        date_time = datetime.strptime(data['date_time'], '%Y-%m-%dT%H:%M')
        max_participants = int(data['max_participants'])
        
        training = Training(
            date_time=date_time,
            max_participants=max_participants
        )
        db_session.add(training)
        db_session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error adding training: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400

@web.route('/training/<int:training_id>', methods=['DELETE'])
@login_required
def delete_training(training_id):
    training = db_session.query(Training).get(training_id)
    if training:
        db_session.delete(training)
        db_session.commit()
    return jsonify({'success': True})

@web.route('/training/<int:training_id>/participants')
@login_required
def get_participants(training_id):
    training = db_session.query(Training).get(training_id)
    if not training:
        return jsonify({'error': 'Training not found'}), 404
    
    participants = []
    for reg in training.registrations:
        # Используем display_name если есть, иначе username
        display_name = reg.display_name or reg.username or 'Без имени'
        
        # Получаем статус team_assigned из таблицы TeamAssignment
        team_assignment = db_session.query(TeamAssignment)\
            .filter_by(training_id=training_id, user_id=reg.user_id)\
            .first()
        team_assigned = team_assignment.team_assigned if team_assignment else False
        
        # Сохранённые предпочтения игрока (для подстановки при отображении и при записи)
        user_prefs = db_session.query(UserPreferences).filter_by(user_id=reg.user_id).first()
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
    
    return jsonify({
        'training_date': training.date_time.strftime('%d.%m.%Y %H:%M'),
        'participants': participants,
        'total': len(participants),
        'max': training.max_participants
    })

@web.route('/training/<int:training_id>/save-jerseys', methods=['POST'])
@login_required
def save_jerseys(training_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        data = request.get_json()
        participant_selections = data.get('participant_selections', {})
        
        if not participant_selections:
            return jsonify({'success': False, 'error': 'No participant selections provided'}), 400
        
        # Сохраняем выбранные майки и команды в базу данных
        for registration in training.registrations:
            # Получаем отображаемое имя для поиска
            display_name = registration.display_name or registration.username
            if display_name in participant_selections:
                selection = participant_selections[display_name]
                
                # Сохраняем майку
                if 'jersey' in selection and selection['jersey'] in ['light', 'dark', 'blue', 'yellow']:
                    registration.jersey_type = JerseyType(selection['jersey'])
                
                # Сохраняем амплуа
                if 'position' in selection and selection['position'] in ['forward', 'defender']:
                    registration.position_type = PositionType(selection['position'])
                
                # Устанавливаем флаг назначения команды в таблице TeamAssignment
                # Для вратарей: достаточно выбрать майку
                # Для полевых игроков: нужно выбрать и майку, и команду, и амплуа
                
                # Получаем или создаем запись в team_assignments
                team_assignment = db_session.query(TeamAssignment)\
                    .filter_by(training_id=training_id, user_id=registration.user_id)\
                    .first()
                
                if not team_assignment:
                    team_assignment = TeamAssignment(
                        training_id=training_id,
                        user_id=registration.user_id,
                        team_assigned=False,
                        assigned_at=None
                    )
                    db_session.add(team_assignment)
                
                if registration.goalkeeper:
                    if 'jersey' in selection and selection['jersey'] in ['light', 'dark', 'blue', 'yellow']:
                        logger.info(f"✅ Параметры сохранены для вратаря {display_name}")
                else:
                    has_jersey = 'jersey' in selection and selection['jersey'] in ['light', 'dark', 'blue', 'yellow']
                    has_position = 'position' in selection and selection['position'] in ['forward', 'defender']
                    
                    logger.info(f"🔍 Проверка для полевого игрока {display_name}: jersey={has_jersey}, position={has_position}")
                    
                    if has_jersey and has_position:
                        logger.info(f"✅ Параметры сохранены для полевого игрока {display_name}")
                    else:
                        logger.warning(f"⚠️ НЕ все параметры выбраны для {display_name}")
        
        # Предпочтения пользователей будут обновлены в функции send_notifications
        # после успешной отправки уведомления
        
        db_session.commit()
        
        return jsonify({'success': True, 'message': 'Майки и команды сохранены в базе данных'})
        
    except Exception as e:
        logger.error(f"Error saving jerseys: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/notify', methods=['POST'])
@login_required
def send_notifications(training_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        data = request.get_json()
        changed_participants = data.get('changed_participants', [])
        
        training_date = training.date_time.strftime('%d.%m.%Y в %H:%M')
        success_count = 0
        failed_count = 0
        
        # Отправляем уведомления участникам:
        # 1. У которых team_assigned=False (еще не получили уведомления)
        # 2. У которых team_assigned=True, но изменились параметры
        logger.info(f"📋 Проверка уведомлений для тренировки {training_id}")
        logger.info(f"📋 Список изменившихся участников: {changed_participants}")
        
        for registration in training.registrations:
            display_name = registration.display_name or registration.username
            
            # Получаем статус распределения из таблицы team_assignments
            team_assignment = db_session.query(TeamAssignment)\
                .filter_by(training_id=training_id, user_id=registration.user_id)\
                .first()
            
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
                db_session.add(team_assignment)
                db_session.commit()
                team_assigned = False
            
            # Проверяем, есть ли у игрока все необходимые параметры для распределения
            has_all_params = bool(
                registration.jersey_type and (
                    registration.goalkeeper or 
                    registration.position_type
                )
            )
            
            # Получаем предпочтения пользователя из базы данных
            user_prefs = db_session.query(UserPreferences).filter_by(user_id=registration.user_id).first()
            
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
                    position_text = "Нап" if registration.position_type.value == 'forward' else "Зщ"
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
                    telegram_response = requests.post(
                        f'https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage',
                        json={
                            'chat_id': registration.user_id,
                            'text': message,
                            'parse_mode': 'Markdown',
                            'reply_markup': keyboard
                        },
                        timeout=10
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
                            db_session.add(user_prefs)
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
                                db_session.add(user_prefs)
                            
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
                            db_session.add(user_prefs)
                        
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
        db_session.commit()
        
        if success_count > 0:
            return jsonify({
                'success': True, 
                'message': f'Уведомления отправлены {success_count} участникам. Ошибок: {failed_count}'
            })
        elif failed_count > 0:
            return jsonify({
                'success': False, 
                'error': f'Ошибки при отправке уведомлений: {failed_count}'
            })
        else:
            return jsonify({
                'success': True, 
                'message': 'Все игроки распределены по командам!'
            })
        
    except Exception as e:
        logger.error(f"Error sending notifications: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/quick-add-players')
@login_required
def get_quick_add_players(training_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        # Получаем ID участников, уже записанных на текущую тренировку
        current_participant_ids = [reg.user_id for reg in training.registrations]
        logger.info(f"Current participants on training {training_id}: {current_participant_ids}")
        
        # Получаем всех игроков из таблицы players
        all_players = db_session.query(Player).all()
        logger.info(f"Total players in database: {len(all_players)}")
        
        # Фильтруем игроков, которые не записаны на текущую тренировку
        available_players = []
        for player in all_players:
            if player.user_id not in current_participant_ids:
                # Проверяем предпочтения пользователя
                user_prefs = db_session.query(UserPreferences).filter_by(user_id=player.user_id).first()
                
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
        
        return jsonify({
            'success': True,
            'players': available_players,
            'total': len(available_players),
            'debug': {
                'current_participants': current_participant_ids,
                'total_players': len(all_players),
                'available_players': len(available_players)
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting quick add players: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/bulk-register', methods=['POST'])
@login_required
def bulk_register_players(training_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        data = request.get_json()
        players = data.get('players', [])
        
        if not players:
            return jsonify({'success': False, 'error': 'No players provided'}), 400
        
        # Проверяем лимит участников
        current_count = len(training.registrations)
        if current_count + len(players) > training.max_participants:
            return jsonify({
                'success': False, 
                'error': f'Превышен лимит участников. Доступно мест: {training.max_participants - current_count}'
            }), 400
        
        # Проверяем лимит вратарей
        current_goalkeepers = sum(1 for reg in training.registrations if reg.goalkeeper)
        new_goalkeepers = sum(1 for player in players if player.get('goalkeeper', False))
        if current_goalkeepers + new_goalkeepers > 2:
            return jsonify({
                'success': False, 
                'error': 'Максимум 2 вратаря на тренировку'
            }), 400
        
        # Добавляем игроков
        added_count = 0
        for player in players:
            # Проверяем, не записан ли уже этот игрок
            existing_reg = db_session.query(Registration)\
                .filter_by(training_id=training_id, user_id=player['user_id'])\
                .first()
            
            if not existing_reg:
                # Получаем предпочтения пользователя
                user_prefs = db_session.query(UserPreferences).filter_by(user_id=player['user_id']).first()
                
                # Создаем новую регистрацию
                registration = Registration(
                    training_id=training_id,
                    user_id=player['user_id'],
                    username=player.get('username', ''),
                    display_name=player.get('display_name') or player.get('username', ''),
                    goalkeeper=player.get('goalkeeper', False),
                    registered_at=datetime.now()
                )
                
                # Применяем предпочтения пользователя
                if user_prefs:
                    registration.jersey_type = user_prefs.preferred_jersey_type
                    registration.position_type = user_prefs.preferred_position_type
                
                db_session.add(registration)
                
                # Обновляем или создаем запись в таблице players
                existing_player = db_session.query(Player).filter_by(user_id=player['user_id']).first()
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
                        user_id=player['user_id'],
                        username=player.get('username', ''),
                        display_name=player.get('display_name') or player.get('username', ''),
                        goalkeeper=player.get('goalkeeper', False),
                        first_registration=datetime.now(),
                        last_registration=datetime.now(),
                        total_registrations=1
                    )
                    db_session.add(new_player)
                
                added_count += 1
        
        db_session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Успешно добавлено {added_count} игроков',
            'added_count': added_count
        })
        
    except Exception as e:
        logger.error(f"Error bulk registering players: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/search-telegram-user', methods=['POST'])
@login_required
def search_telegram_user():
    """Ищет пользователя в Telegram по username"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip().replace('@', '')
        
        if not username:
            return jsonify({'success': False, 'error': 'Username не указан'}), 400
        
        # Сначала проверяем, есть ли пользователь в нашей базе
        existing_player = db_session.query(Player).filter_by(username=username).first()
        if existing_player:
            return jsonify({
                'success': True,
                'user': {
                    'user_id': existing_player.user_id,
                    'username': existing_player.username,
                    'display_name': existing_player.display_name,
                    'first_name': existing_player.display_name or existing_player.username,
                    'goalkeeper': existing_player.goalkeeper
                },
                'found_in_db': True
            })
        
        # Если пользователя нет в базе, возвращаем возможность добавить его вручную
        # Для этого создаем временный user_id на основе username
        # При добавлении реального игрока через бота, этот user_id будет обновлен
        return jsonify({
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
        })
        
    except Exception as e:
        logger.error(f"Error searching telegram user: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/add-player-by-username', methods=['POST'])
@login_required
def add_player_by_username():
    """Добавляет игрока по username или display_name напрямую в базу"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip().replace('@', '')
        display_name = data.get('display_name', '').strip()
        goalkeeper = data.get('goalkeeper', False)
        
        # Проверяем, что указан хотя бы один из параметров
        if not username and not display_name:
            return jsonify({'success': False, 'error': 'Необходимо указать либо Telegram логин, либо имя игрока'}), 400
        
        # Если есть username, проверяем, есть ли уже такой пользователь в базе
        if username:
            existing_player = db_session.query(Player).filter_by(username=username).first()
            if existing_player:
                return jsonify({
                    'success': True,
                    'user': {
                        'user_id': existing_player.user_id,
                        'username': existing_player.username,
                        'display_name': existing_player.display_name,
                        'goalkeeper': existing_player.goalkeeper
                    },
                    'message': 'Пользователь уже есть в базе'
                })
        
        # Создаем нового игрока с временным user_id
        # Используем отрицательный hash от username (если есть) или display_name как временный user_id
        identifier = username if username else display_name
        temp_user_id = -abs(hash(identifier + str(datetime.now().timestamp())) % (10 ** 10))
        
        # Проверяем, что такой user_id еще не существует (маловероятно, но на всякий случай)
        while db_session.query(Player).filter_by(user_id=temp_user_id).first():
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
        
        db_session.add(new_player)
        db_session.commit()
        
        return jsonify({
            'success': True,
            'user': {
                'user_id': temp_user_id,
                'username': username if username else None,
                'display_name': display_name if display_name else username,
                'goalkeeper': goalkeeper
            },
            'message': 'Игрок добавлен в базу'
        })
        
    except Exception as e:
        logger.error(f"Error adding player by username: {e}")
        import traceback
        traceback.print_exc()
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/participant/<int:participant_id>', methods=['DELETE'])
@login_required
def remove_participant(training_id, participant_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        registration = db_session.query(Registration)\
            .filter_by(id=participant_id, training_id=training_id)\
            .first()
        
        if not registration:
            return jsonify({'success': False, 'error': 'Participant not found'}), 404
        
        participant_name = registration.display_name or registration.username or 'Без имени'
        
        # Удаляем запись из team_assignments
        team_assignment = db_session.query(TeamAssignment)\
            .filter_by(training_id=training_id, user_id=registration.user_id)\
            .first()
        
        if team_assignment:
            db_session.delete(team_assignment)
            logger.info(f"🗑️ Удалена запись TeamAssignment для участника {participant_name}")
        
        # Удаляем регистрацию
        db_session.delete(registration)
        db_session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Участник {participant_name} удален из тренировки'
        })
        
    except Exception as e:
        logger.error(f"Error removing participant: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/participant/<int:participant_id>/rename', methods=['POST'])
@login_required
def rename_participant(training_id, participant_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        registration = db_session.query(Registration)\
            .filter_by(id=participant_id, training_id=training_id)\
            .first()
        
        if not registration:
            return jsonify({'success': False, 'error': 'Participant not found'}), 404
        
        data = request.get_json()
        new_name_input = data.get('name', '').strip()
        is_goalkeeper = data.get('goalkeeper', False)
        
        # Если новое имя пустое, используем текущее отображаемое имя
        new_name = new_name_input or registration.display_name or registration.username or 'Без имени'
        
        # Проверяем лимит вратарей (максимум 2)
        if is_goalkeeper:
            current_goalkeepers = db_session.query(Registration)\
                .filter_by(training_id=training_id, goalkeeper=True)\
                .filter(Registration.id != participant_id)\
                .count()
            if current_goalkeepers >= 2:
                return jsonify({'success': False, 'error': 'Максимум 2 вратаря на тренировку'}), 400
        
        # Обновляем отображаемое имя и статус вратаря в регистрации
        registration.display_name = new_name
        registration.goalkeeper = is_goalkeeper
        
        # Обновляем отображаемое имя и статус вратаря в предпочтениях пользователя для будущих записей
        user_prefs = db_session.query(UserPreferences).filter_by(user_id=registration.user_id).first()
        if not user_prefs:
            user_prefs = UserPreferences(user_id=registration.user_id)
            db_session.add(user_prefs)
        user_prefs.display_name = new_name
        user_prefs.goalkeeper = is_goalkeeper
        
        db_session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'Имя участника изменено на "{new_name}"',
            'new_name': new_name
        })
        
    except Exception as e:
        logger.error(f"Error renaming participant: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/participant/<int:participant_id>/remember-preferences', methods=['POST'])
@login_required
def remember_participant_preferences(training_id, participant_id):
    """Сохраняет выбранные параметры игрока (майка, амплуа) в предпочтения для последующих записей."""
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        registration = db_session.query(Registration)\
            .filter_by(id=participant_id, training_id=training_id)\
            .first()
        
        if not registration:
            return jsonify({'success': False, 'error': 'Participant not found'}), 404
        
        data = request.get_json() or {}
        jersey_type = data.get('jersey_type')
        position_type = data.get('position_type')
        
        if jersey_type not in ('light', 'dark', 'blue', 'yellow') and jersey_type is not None:
            return jsonify({'success': False, 'error': 'Invalid jersey_type'}), 400
        if position_type not in ('forward', 'defender') and position_type is not None and not registration.goalkeeper:
            return jsonify({'success': False, 'error': 'Invalid position_type'}), 400
        
        user_prefs = db_session.query(UserPreferences).filter_by(user_id=registration.user_id).first()
        if not user_prefs:
            user_prefs = UserPreferences(user_id=registration.user_id)
            db_session.add(user_prefs)
        
        if jersey_type is not None:
            user_prefs.preferred_jersey_type = JerseyType(jersey_type)
        if not registration.goalkeeper and position_type is not None:
            user_prefs.preferred_position_type = PositionType(position_type)
        
        db_session.commit()
        
        participant_name = registration.display_name or registration.username or 'Без имени'
        return jsonify({
            'success': True,
            'message': f'Предпочтения для {participant_name} сохранены'
        })
    except Exception as e:
        logger.error(f"Error saving participant preferences: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/participant/<int:participant_id>/assign-team', methods=['POST'])
@login_required
def assign_team(training_id, participant_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        registration = db_session.query(Registration)\
            .filter_by(id=participant_id, training_id=training_id)\
            .first()
        
        if not registration:
            return jsonify({'success': False, 'error': 'Participant not found'}), 404
        
        # Устанавливаем флаг назначения команды
        registration.team_assigned = True
        
        db_session.commit()
        
        participant_name = registration.display_name or registration.username or 'Без имени'
        
        return jsonify({
            'success': True,
            'message': f'Команда назначена для {participant_name}'
        })
        
    except Exception as e:
        logger.error(f"Error assigning team: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/training/<int:training_id>/participant/<int:participant_id>/mark-paid', methods=['POST'])
@login_required
def mark_participant_paid(training_id, participant_id):
    try:
        training = db_session.query(Training).get(training_id)
        if not training:
            return jsonify({'success': False, 'error': 'Training not found'}), 404
        
        registration = db_session.query(Registration)\
            .filter_by(id=participant_id, training_id=training_id)\
            .first()
        
        if not registration:
            return jsonify({'success': False, 'error': 'Participant not found'}), 404
        
        # Проверяем, что это не вратарь
        if registration.goalkeeper:
            return jsonify({'success': False, 'error': 'Goalkeeper payment is not tracked'}), 400
        
        # Устанавливаем флаг оплаты
        registration.paid = True
        
        db_session.commit()
        
        participant_name = registration.display_name or registration.username or 'Без имени'
        
        return jsonify({
            'success': True,
            'message': f'Статус оплаты обновлен для {participant_name}'
        })
        
    except Exception as e:
        logger.error(f"Error marking participant as paid: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/health')
def health_check():
    """Health check endpoint для Docker"""
    try:
        # Проверяем подключение к базе данных
        from sqlalchemy import text
        db_session.execute(text('SELECT 1'))
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 503

@web.route('/send-weekly-post', methods=['POST'])
@login_required
def send_weekly_post():
    """Отправляет еженедельный пост о тренировке"""
    try:
        # Получаем экземпляр бота из глобального контекста
        # Это требует доступа к боту, который запущен в основном приложении
        import asyncio
        from ..bot.handlers import start_bot
        
        # Создаем временный бот для отправки поста
        async def send_post():
            try:
                # Создаем приложение бота
                from telegram.ext import Application
                application = Application.builder().token(Config.TELEGRAM_TOKEN).build()
                await application.initialize()
                await application.start()
                
                # Отправляем пост
                success = await send_weekly_training_post(application.bot)
                
                # Останавливаем приложение
                await application.stop()
                await application.shutdown()
                
                return success
            except Exception as e:
                logger.error(f"Ошибка при отправке поста: {e}")
                return False
        
        # Запускаем асинхронную функцию
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(send_post())
        loop.close()
        
        if success:
            return jsonify({
                'success': True, 
                'message': 'Еженедельный пост успешно отправлен!'
            })
        else:
            return jsonify({
                'success': False, 
                'error': 'Не удалось отправить пост. Проверьте настройки CHANNEL_ID и права бота.'
            }), 500
            
    except Exception as e:
        logger.error(f"Ошибка в send_weekly_post: {e}")
        return jsonify({
            'success': False, 
            'error': f'Ошибка: {str(e)}'
        }), 500

@web.route('/messages')
@login_required
def messages_page():
    """Страница управления сообщениями"""
    messages = db_session.query(ScheduledMessage)\
        .order_by(ScheduledMessage.created_at.desc())\
        .all()
    return render_template('messages.html', messages=messages)

@web.route('/messages', methods=['POST'])
@login_required
def create_message():
    """Создание нового сообщения"""
    try:
        data = request.get_json()
        message_text = data.get('message_text', '').strip()
        
        if not message_text:
            return jsonify({'success': False, 'error': 'Текст сообщения не может быть пустым'}), 400
        
        send_immediately = data.get('send_immediately', False)
        scheduled_time = None
        repeat_type = RepeatType.ONCE
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
        
        db_session.add(message)
        db_session.commit()
        
        # Если нужно отправить немедленно
        if send_immediately:
            try:
                if not Config.CHANNEL_ID:
                    return jsonify({
                        'success': False,
                        'error': 'CHANNEL_ID не настроен. Сообщение создано, но не отправлено.'
                    }), 400
                
                # Валидация формата CHANNEL_ID
                try:
                    channel_id_int = int(Config.CHANNEL_ID)
                    # Для каналов и супергрупп ID должен начинаться с -100
                    if channel_id_int > 0:
                        logger.warning(f"⚠️ CHANNEL_ID ({Config.CHANNEL_ID}) выглядит как личный чат. Для каналов/групп ID должен начинаться с -100")
                except (ValueError, TypeError):
                    return jsonify({
                        'success': False,
                        'error': f'CHANNEL_ID имеет неверный формат: {Config.CHANNEL_ID}. Должно быть числовое значение.'
                    }), 400
                
                # Отправляем сообщение через Telegram Bot API
                send_params = {
                    'chat_id': Config.CHANNEL_ID,
                    'text': message.message_text
                }
                
                # Добавляем message_thread_id только если он задан
                if Config.MESSAGE_THREAD_ID:
                    send_params['message_thread_id'] = int(Config.MESSAGE_THREAD_ID)
                
                telegram_response = requests.post(
                    f'https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage',
                    json=send_params,
                    timeout=10
                )
                
                # Проверяем ответ от Telegram API
                response_data = telegram_response.json() if telegram_response.headers.get('content-type', '').startswith('application/json') else {}
                
                if telegram_response.status_code == 200 and response_data.get('ok', False):
                    # Обновляем время последней отправки
                    message.last_sent_at = datetime.now()
                    db_session.commit()
                    logger.info(f"✅ Сообщение #{message.id} отправлено немедленно в канал {Config.CHANNEL_ID}")
                else:
                    # Извлекаем описание ошибки из ответа
                    error_description = response_data.get('description', telegram_response.text)
                    error_code = response_data.get('error_code', 'unknown')
                    logger.error(f"❌ Ошибка отправки сообщения (код {error_code}): {error_description}")
                    
                    # Формируем понятное сообщение об ошибке
                    if 'chat not found' in error_description.lower():
                        error_message = f'Канал не найден. Проверьте, что:\n1. CHANNEL_ID указан правильно (должен начинаться с -100 для каналов/супергрупп)\n2. Бот добавлен в канал/группу как администратор\n3. Бот имеет права на отправку сообщений\n\nТекущий CHANNEL_ID: {Config.CHANNEL_ID}'
                    elif 'bot was blocked' in error_description.lower():
                        error_message = 'Бот заблокирован в канале. Добавьте бота обратно в канал.'
                    elif 'not enough rights' in error_description.lower():
                        error_message = 'У бота недостаточно прав. Убедитесь, что бот является администратором канала с правами на отправку сообщений.'
                    else:
                        error_message = f'Не удалось отправить сообщение: {error_description}'
                    
                    return jsonify({
                        'success': False,
                        'error': error_message
                    }), 500
                    
            except Exception as e:
                logger.error(f"Ошибка при немедленной отправке: {e}")
                return jsonify({
                    'success': False,
                    'error': f'Ошибка при отправке: {str(e)}'
                }), 500
        
        return jsonify({
            'success': True,
            'message': 'Сообщение успешно создано'
        })
        
    except Exception as e:
        logger.error(f"Ошибка при создании сообщения: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/messages/<int:message_id>')
@login_required
def get_message(message_id):
    """Получение сообщения по ID"""
    try:
        message = db_session.query(ScheduledMessage).get(message_id)
        if not message:
            return jsonify({'success': False, 'error': 'Сообщение не найдено'}), 404
        
        return jsonify({
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
        })
    except Exception as e:
        logger.error(f"Ошибка при получении сообщения: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/messages/<int:message_id>', methods=['PUT'])
@login_required
def update_message(message_id):
    """Обновление сообщения"""
    try:
        message = db_session.query(ScheduledMessage).get(message_id)
        if not message:
            return jsonify({'success': False, 'error': 'Сообщение не найдено'}), 404
        
        data = request.get_json()
        message_text = data.get('message_text', '').strip()
        
        if not message_text:
            return jsonify({'success': False, 'error': 'Текст сообщения не может быть пустым'}), 400
        
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
        
        db_session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Сообщение успешно обновлено'
        })
        
    except Exception as e:
        logger.error(f"Ошибка при обновлении сообщения: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/messages/<int:message_id>', methods=['DELETE'])
@login_required
def delete_message(message_id):
    """Удаление сообщения"""
    try:
        message = db_session.query(ScheduledMessage).get(message_id)
        if not message:
            return jsonify({'success': False, 'error': 'Сообщение не найдено'}), 404
        
        db_session.delete(message)
        db_session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Сообщение успешно удалено'
        })
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/messages/<int:message_id>/toggle', methods=['POST'])
@login_required
def toggle_message(message_id):
    """Активация/деактивация сообщения"""
    try:
        message = db_session.query(ScheduledMessage).get(message_id)
        if not message:
            return jsonify({'success': False, 'error': 'Сообщение не найдено'}), 404
        
        message.is_active = not message.is_active
        db_session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Статус сообщения изменен',
            'is_active': message.is_active
        })
    except Exception as e:
        logger.error(f"Ошибка при изменении статуса сообщения: {e}")
        db_session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@web.route('/messages/<int:message_id>/send-now', methods=['POST'])
@login_required
def send_message_now(message_id):
    """Немедленная отправка сообщения"""
    try:
        message = db_session.query(ScheduledMessage).get(message_id)
        if not message:
            return jsonify({'success': False, 'error': 'Сообщение не найдено'}), 404
        
        if not Config.CHANNEL_ID:
            return jsonify({
                'success': False,
                'error': 'CHANNEL_ID не настроен. Проверьте настройки.'
            }), 400
        
        # Валидация формата CHANNEL_ID
        try:
            channel_id_int = int(Config.CHANNEL_ID)
            # Для каналов и супергрупп ID должен начинаться с -100
            if channel_id_int > 0:
                logger.warning(f"⚠️ CHANNEL_ID ({Config.CHANNEL_ID}) выглядит как личный чат. Для каналов/групп ID должен начинаться с -100")
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': f'CHANNEL_ID имеет неверный формат: {Config.CHANNEL_ID}. Должно быть числовое значение.'
            }), 400
        
        # Отправляем сообщение через Telegram Bot API
        send_params = {
            'chat_id': Config.CHANNEL_ID,
            'text': message.message_text
        }
        
        # Добавляем message_thread_id только если он задан
        if Config.MESSAGE_THREAD_ID:
            send_params['message_thread_id'] = int(Config.MESSAGE_THREAD_ID)
        
        telegram_response = requests.post(
            f'https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage',
            json=send_params,
            timeout=10
        )
        
        # Проверяем ответ от Telegram API
        response_data = telegram_response.json() if telegram_response.headers.get('content-type', '').startswith('application/json') else {}
        
        if telegram_response.status_code == 200 and response_data.get('ok', False):
            # Обновляем время последней отправки
            message.last_sent_at = datetime.now()
            db_session.commit()
            logger.info(f"✅ Сообщение #{message.id} отправлено немедленно в канал {Config.CHANNEL_ID}")
            return jsonify({
                'success': True,
                'message': 'Сообщение успешно отправлено'
            })
        else:
            # Извлекаем описание ошибки из ответа
            error_description = response_data.get('description', telegram_response.text)
            error_code = response_data.get('error_code', 'unknown')
            logger.error(f"❌ Ошибка отправки сообщения (код {error_code}): {error_description}")
            
            # Формируем понятное сообщение об ошибке
            if 'chat not found' in error_description.lower():
                error_message = f'Канал не найден. Проверьте, что:\n1. CHANNEL_ID указан правильно (должен начинаться с -100 для каналов/супергрупп)\n2. Бот добавлен в канал/группу как администратор\n3. Бот имеет права на отправку сообщений\n\nТекущий CHANNEL_ID: {Config.CHANNEL_ID}'
            elif 'bot was blocked' in error_description.lower():
                error_message = 'Бот заблокирован в канале. Добавьте бота обратно в канал.'
            elif 'not enough rights' in error_description.lower():
                error_message = 'У бота недостаточно прав. Убедитесь, что бот является администратором канала с правами на отправку сообщений.'
            else:
                error_message = f'Не удалось отправить сообщение: {error_description}'
            
            return jsonify({
                'success': False,
                'error': error_message
            }), 500
            
    except Exception as e:
        logger.error(f"Ошибка в send_message_now: {e}")
        db_session.rollback()
        return jsonify({
            'success': False,
            'error': f'Ошибка: {str(e)}'
        }), 500

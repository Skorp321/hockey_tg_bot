from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime, timedelta
from functools import wraps
import requests
from ..models import Training, Registration, JerseyType, TeamType, UserPreferences, Player
from ..database import db_session
from ..config import Config
from ..bot.weekly_posts import send_weekly_training_post

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
        print(f"Error adding training: {e}")
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
        participants.append({
            'id': reg.id,
            'user_id': reg.user_id,
            'username': reg.username or 'Без имени',
            'display_name': reg.display_name,
            'name': display_name,
            'registered_at': reg.registered_at.strftime('%d.%m.%Y %H:%M'),
            'jersey_type': reg.jersey_type.value if reg.jersey_type else None,
            'team_type': reg.team_type.value if reg.team_type else None,
            'goalkeeper': reg.goalkeeper,
            'team_assigned': reg.team_assigned,
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
                if 'jersey' in selection and selection['jersey'] in ['light', 'dark']:
                    registration.jersey_type = JerseyType(selection['jersey'])
                
                # Сохраняем команду
                if 'team' in selection and selection['team'] in ['first', 'second']:
                    registration.team_type = TeamType(selection['team'])
                
                # Устанавливаем флаг назначения команды
                # Для вратарей: достаточно выбрать майку
                # Для полевых игроков: нужно выбрать и майку, и команду
                if registration.goalkeeper:
                    if 'jersey' in selection and selection['jersey'] in ['light', 'dark']:
                        registration.team_assigned = True
                else:
                    if ('jersey' in selection and selection['jersey'] in ['light', 'dark'] and 
                        'team' in selection and selection['team'] in ['first', 'second']):
                        registration.team_assigned = True
        
        # Сохраняем предпочтения пользователей для будущих тренировок
        for registration in training.registrations:
            display_name = registration.display_name or registration.username
            if display_name in participant_selections:
                selection = participant_selections[display_name]
                
                # Ищем или создаем предпочтения пользователя
                user_prefs = db_session.query(UserPreferences).filter_by(user_id=registration.user_id).first()
                if not user_prefs:
                    user_prefs = UserPreferences(user_id=registration.user_id)
                    db_session.add(user_prefs)
                
                # Обновляем предпочтения
                if 'jersey' in selection and selection['jersey'] in ['light', 'dark']:
                    user_prefs.preferred_jersey_type = JerseyType(selection['jersey'])
                if 'team' in selection and selection['team'] in ['first', 'second']:
                    user_prefs.preferred_team_type = TeamType(selection['team'])
        
        db_session.commit()
        
        return jsonify({'success': True, 'message': 'Майки и команды сохранены в базе данных'})
        
    except Exception as e:
        print(f"Error saving jerseys: {e}")
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
        
        # Отправляем уведомления только изменившимся участникам
        for registration in training.registrations:
            display_name = registration.display_name or registration.username
            if display_name in changed_participants and registration.jersey_type and registration.team_type:
                # Формируем индивидуальное сообщение для участника
                jersey_emoji = "⚪" if registration.jersey_type.value == 'light' else "⚫"
                team_emoji = "1️⃣" if registration.team_type.value == 'first' else "2️⃣"
                
                message = f"🏒 *Уведомление о тренировке*\n\n"
                message += f"📅 Дата: {training_date}\n"
                message += f"🎯 Ваша майка: {jersey_emoji}\n"
                message += f"👥 Ваша пятерка: {team_emoji}\n"
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
                        print(f"✅ Уведомление отправлено участнику {display_name} ({registration.jersey_type.value})")
                    else:
                        failed_count += 1
                        print(f"❌ Ошибка отправки участнику {display_name}: {telegram_response.text}")
                        
                except Exception as e:
                    failed_count += 1
                    print(f"❌ Ошибка отправки участнику {display_name}: {e}")
        
        # Логируем общий результат
        print(f"📊 Итоги отправки уведомлений для тренировки {training_id}")
        print(f"✅ Успешно отправлено: {success_count}")
        print(f"❌ Ошибок отправки: {failed_count}")
        
        if success_count > 0:
            return jsonify({
                'success': True, 
                'message': f'Уведомления отправлены {success_count} участникам. Ошибок: {failed_count}'
            })
        else:
            return jsonify({
                'success': False, 
                'error': 'Не удалось отправить ни одного уведомления'
            })
        
    except Exception as e:
        print(f"Error sending notifications: {e}")
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
        print(f"Current participants on training {training_id}: {current_participant_ids}")
        
        # Получаем всех игроков из таблицы players
        all_players = db_session.query(Player).all()
        print(f"Total players in database: {len(all_players)}")
        
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
        
        print(f"Available players for quick add: {len(available_players)}")
        
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
        print(f"Error getting quick add players: {e}")
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
                    registration.team_type = user_prefs.preferred_team_type
                
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
        print(f"Error bulk registering players: {e}")
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
        
        # Удаляем регистрацию
        db_session.delete(registration)
        db_session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Участник {participant_name} удален из тренировки'
        })
        
    except Exception as e:
        print(f"Error removing participant: {e}")
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
        print(f"Error renaming participant: {e}")
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
        print(f"Error assigning team: {e}")
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
        
        # Устанавливаем флаг оплаты
        registration.paid = True
        
        db_session.commit()
        
        participant_name = registration.display_name or registration.username or 'Без имени'
        
        return jsonify({
            'success': True,
            'message': f'Статус оплаты обновлен для {participant_name}'
        })
        
    except Exception as e:
        print(f"Error marking participant as paid: {e}")
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
                print(f"Ошибка при отправке поста: {e}")
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
        print(f"Ошибка в send_weekly_post: {e}")
        return jsonify({
            'success': False, 
            'error': f'Ошибка: {str(e)}'
        }), 500

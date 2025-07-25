from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime
from functools import wraps
import requests
from ..models import Training, Registration, JerseyType
from ..database import db_session
from ..config import Config

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
    trainings = db_session.query(Training)\
        .filter(Training.date_time > datetime.now())\
        .order_by(Training.date_time)\
        .all()
    return render_template('schedule.html', trainings=trainings)

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
        participants.append({
            'username': reg.username or 'Без имени',
            'registered_at': reg.registered_at.strftime('%d.%m.%Y %H:%M'),
            'jersey_type': reg.jersey_type.value if reg.jersey_type else None
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
        
        # Сохраняем выбранные майки в базу данных
        for registration in training.registrations:
            if registration.username in participant_selections:
                jersey_type = participant_selections[registration.username]
                if jersey_type in ['light', 'dark']:
                    registration.jersey_type = JerseyType(jersey_type)
        
        db_session.commit()
        
        return jsonify({'success': True, 'message': 'Майки сохранены в базе данных'})
        
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
            if registration.username in changed_participants and registration.jersey_type:
                # Формируем индивидуальное сообщение для участника
                jersey_emoji = "⚪" if registration.jersey_type.value == 'light' else "⚫"
                
                message = f"🏒 *Уведомление о тренировке*\n\n"
                message += f"📅 Дата: {training_date}\n"
                message += f"🎯 Ваша майка: {jersey_emoji}\n"
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
                        print(f"✅ Уведомление отправлено участнику {registration.username} ({registration.jersey_type.value})")
                    else:
                        failed_count += 1
                        print(f"❌ Ошибка отправки участнику {registration.username}: {telegram_response.text}")
                        
                except Exception as e:
                    failed_count += 1
                    print(f"❌ Ошибка отправки участнику {registration.username}: {e}")
        
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
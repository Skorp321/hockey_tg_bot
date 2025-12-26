from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Enum, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum
import json

Base = declarative_base()

class JerseyType(enum.Enum):
    LIGHT = "light"
    DARK = "dark"

class TeamType(enum.Enum):
    FIRST = "first"
    SECOND = "second"

class PositionType(enum.Enum):
    FORWARD = "forward"  # Нап
    DEFENDER = "defender"  # Зщ

class RepeatType(enum.Enum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

class TeamAssignment(Base):
    __tablename__ = 'team_assignments'
    
    id = Column(Integer, primary_key=True)
    training_id = Column(Integer, ForeignKey('trainings.id'), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    team_assigned = Column(Boolean, default=False, nullable=False)  # Статус распределения на эту тренировку
    assigned_at = Column(DateTime, nullable=True)  # Время когда было назначено распределение
    
    # Связи
    training = relationship('Training', overlaps="team_assignments")
    
    # Уникальный индекс для пары training_id + user_id
    __table_args__ = (
        {'extend_existing': True}
    )

class Training(Base):
    __tablename__ = 'trainings'
    
    id = Column(Integer, primary_key=True)
    date_time = Column(DateTime, nullable=False)
    max_participants = Column(Integer, default=10)
    registrations = relationship('Registration', back_populates='training', cascade='all, delete-orphan')
    team_assignments = relationship('TeamAssignment', cascade='all, delete-orphan')

class Registration(Base):
    __tablename__ = 'registrations'
    
    id = Column(Integer, primary_key=True)
    training_id = Column(Integer, ForeignKey('trainings.id'), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    username = Column(String(100))
    display_name = Column(String(100), nullable=True)  # Отображаемое имя игрока
    registered_at = Column(DateTime, default=datetime.now, nullable=False)
    jersey_type = Column(Enum(JerseyType), nullable=True)  # Новое поле для типа майки
    team_type = Column(Enum(TeamType), nullable=True)  # Новое поле для выбора команды
    position_type = Column(Enum(PositionType), nullable=True)  # Поле для амплуа (Нап/Зщ)
    goalkeeper = Column(Boolean, default=False, nullable=False)  # Поле для обозначения вратаря
    paid = Column(Boolean, default=False, nullable=False)  # Поле для отметки "Оплатил тренировку"
    last_payment_reminder = Column(DateTime, nullable=True)  # Время последнего напоминания об оплате
    
    training = relationship('Training', back_populates='registrations')

class Player(Base):
    __tablename__ = 'players'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, unique=True)  # Telegram user ID
    username = Column(String(100), nullable=True)  # Telegram username
    display_name = Column(String(100), nullable=True)  # Отображаемое имя игрока
    goalkeeper = Column(Boolean, default=False, nullable=False)  # Статус вратаря
    first_registration = Column(DateTime, nullable=False)  # Дата первой регистрации
    last_registration = Column(DateTime, nullable=False)  # Дата последней регистрации
    total_registrations = Column(Integer, default=1, nullable=False)  # Общее количество записей
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

class UserPreferences(Base):
    __tablename__ = 'user_preferences'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, unique=True)
    preferred_jersey_type = Column(Enum(JerseyType), nullable=True)  # Предпочтительный цвет майки
    preferred_team_type = Column(Enum(TeamType), nullable=True)  # Предпочтительная команда
    preferred_position_type = Column(Enum(PositionType), nullable=True)  # Предпочтительное амплуа
    display_name = Column(String(100), nullable=True)  # Последнее переименованное имя пользователя
    goalkeeper = Column(Boolean, default=False, nullable=False)  # Предпочтение быть вратарем
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

class ScheduledMessage(Base):
    __tablename__ = 'scheduled_messages'
    
    id = Column(Integer, primary_key=True)
    message_text = Column(Text, nullable=False)  # Текст сообщения
    send_immediately = Column(Boolean, default=False, nullable=False)  # Флаг немедленной отправки
    scheduled_time = Column(DateTime, nullable=True)  # Время первой отправки
    repeat_type = Column(Enum(RepeatType), nullable=False, default=RepeatType.ONCE)  # Тип повторения
    repeat_days = Column(String(100), nullable=True)  # Дни недели для еженедельного повторения (JSON)
    is_active = Column(Boolean, default=True, nullable=False)  # Активна ли задача
    last_sent_at = Column(DateTime, nullable=True)  # Время последней отправки
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    
    def get_repeat_days(self):
        """Возвращает список дней недели из JSON строки"""
        if self.repeat_days:
            try:
                return json.loads(self.repeat_days)
            except:
                return []
        return []
    
    def set_repeat_days(self, days):
        """Устанавливает дни недели в JSON строку"""
        if days:
            self.repeat_days = json.dumps(days)
        else:
            self.repeat_days = None 
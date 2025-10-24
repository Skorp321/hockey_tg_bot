from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Enum, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum

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

class Training(Base):
    __tablename__ = 'trainings'
    
    id = Column(Integer, primary_key=True)
    date_time = Column(DateTime, nullable=False)
    max_participants = Column(Integer, default=10)
    registrations = relationship('Registration', back_populates='training', cascade='all, delete-orphan')

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
    team_assigned = Column(Boolean, default=False, nullable=False)  # Поле для отметки "Команда назначена"
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
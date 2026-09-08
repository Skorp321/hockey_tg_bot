from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, DateTime, Date, Time, ForeignKey, Enum,
    Boolean, Text, UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum
import json

Base = declarative_base()


class _EnumByValueOrName(Enum):
    """Enum: в БД пишем value (lowercase), при чтении принимаем и value, и name (FORWARD/forward)."""

    def _object_value_for_elem(self, elem: str):
        if elem is None:
            return None
        try:
            return self._object_lookup[elem]
        except KeyError:
            pass
        # Пробуем по имени (для старых записей из БД: 'LIGHT', 'FORWARD' и т.д.)
        enum_class = self.enum_class
        if enum_class is not None:
            try:
                return enum_class[elem]
            except (KeyError, TypeError):
                pass
            for m in enum_class:
                if m.name == elem:
                    return m
        raise LookupError(
            "'%s' is not among the defined enum values. "
            "Possible values: %s" % (elem, [e.value for e in self.enum_class])
        )


def _enum_by_value_or_name(enum_class):
    return _EnumByValueOrName(
        enum_class,
        values_callable=lambda x: [e.value for e in x],
    )


# Порядок объявления = порядок групп в опубликованном списке.
class JerseyType(enum.Enum):
    LIGHT = "light"  # Белая
    YELLOW = "yellow"  # Желтая
    DARK = "dark"   # Черная
    RED = "red"  # Красная
    BLUE = "blue"   # Синяя

# Порядок объявления = порядок игроков внутри цветной группы.
class PositionType(enum.Enum):
    LW = "lw"  # Левый нападающий
    C = "c"  # Центральный нападающий
    RW = "rw"  # Правый нападающий
    LD = "ld"  # Левый защитник
    RD = "rd"  # Правый защитник

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
    # Поля шапки списка. NULL = взять значение по умолчанию из app_settings.
    end_time = Column(Time, nullable=True)  # Время окончания, вторая половина «21.30 - 23.00»
    venue = Column(String(200), nullable=True)  # Арена
    signup_deadline_text = Column(Text, nullable=True)  # Свободный текст дедлайна в шапке
    registrations = relationship('Registration', back_populates='training', cascade='all, delete-orphan')
    team_assignments = relationship('TeamAssignment', cascade='all, delete-orphan')
    messages = relationship('TrainingMessage', back_populates='training', cascade='all, delete-orphan')

class Registration(Base):
    __tablename__ = 'registrations'
    
    id = Column(Integer, primary_key=True)
    training_id = Column(Integer, ForeignKey('trainings.id'), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    username = Column(String(100))
    display_name = Column(String(100), nullable=True)  # Отображаемое имя игрока
    registered_at = Column(DateTime, default=datetime.now, nullable=False)
    jersey_type = Column(_enum_by_value_or_name(JerseyType), nullable=True)  # Поле для типа майки
    position_type = Column(_enum_by_value_or_name(PositionType), nullable=True)  # Поле для амплуа (Нап/Зщ)
    goalkeeper = Column(Boolean, default=False, nullable=False)  # Поле для обозначения вратаря
    paid = Column(Boolean, default=False, nullable=False)  # Поле для отметки "Оплатил тренировку"
    last_payment_reminder = Column(DateTime, nullable=True)  # Время последнего напоминания об оплате
    # Нажал ли игрок «Записаться» сам. Запись, созданную администратором через
    # «Быстрое добавление», это не считает подтверждением: список показывает, кто
    # ДОЛЖЕН быть на тренировке, а галочка — кто подтвердил, что придёт.
    self_registered = Column(Boolean, default=False, nullable=False)
    
    training = relationship('Training', back_populates='registrations')

class Player(Base):
    __tablename__ = 'players'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, unique=True)  # Telegram user ID
    username = Column(String(100), nullable=True)  # Telegram username
    display_name = Column(String(100), nullable=True)  # Отображаемое имя игрока
    goalkeeper = Column(Boolean, default=False, nullable=False)  # Статус вратаря
    # Входит ли игрок в постоянный состав. Цвет и амплуа при этом берутся из
    # UserPreferences — здесь их дублировать не нужно.
    is_roster_member = Column(Boolean, default=False, nullable=False)
    first_registration = Column(DateTime, nullable=False)  # Дата первой регистрации
    last_registration = Column(DateTime, nullable=False)  # Дата последней регистрации
    total_registrations = Column(Integer, default=1, nullable=False)  # Общее количество записей
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

class UserPreferences(Base):
    __tablename__ = 'user_preferences'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, unique=True)
    preferred_jersey_type = Column(_enum_by_value_or_name(JerseyType), nullable=True)  # Предпочтительный цвет майки
    preferred_position_type = Column(_enum_by_value_or_name(PositionType), nullable=True)  # Предпочтительное амплуа
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


# Таблицы ниже добавлены вместе со списком состава. Они приезжают на прод сами:
# create_all(checkfirst=True) создаёт отсутствующие таблицы (в отличие от колонок).
# По этой же причине здесь намеренно нет enum-колонок — иначе create_all попытался бы
# выпустить CREATE TYPE, а это как раз хрупкий путь, который лечится миграциями.

class AppSetting(Base):
    """Настройки, которые нужно менять без передеплоя (дефолты шапки, лимиты)."""
    __tablename__ = 'app_settings'

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class TrainingMessage(Base):
    """Опубликованное в Telegram сообщение со списком — чтобы потом его редактировать."""
    __tablename__ = 'training_messages'

    id = Column(Integer, primary_key=True)
    training_id = Column(Integer, ForeignKey('trainings.id'), nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=False)
    thread_id = Column(BigInteger, nullable=True)  # топик супергруппы, если используется
    # Хеш последнего отправленного текста: если не изменился, Telegram вообще не дёргаем.
    # Заодно снимает все ошибки "message is not modified".
    text_hash = Column(String(64), nullable=True)
    last_edit_at = Column(DateTime, nullable=True)
    disabled = Column(Boolean, default=False, nullable=False)  # бота выгнали из канала и т.п.
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    training = relationship('Training', back_populates='messages')

    __table_args__ = (UniqueConstraint('training_id', 'chat_id', name='uq_training_message'),)


class SeasonPass(Base):
    """Абонемент на календарный месяц."""
    __tablename__ = 'season_passes'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    # Всегда первое число месяца: одна колонка вместо пары (год, месяц) и одно сравнение.
    period_start = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    created_by = Column(String(20), nullable=True)  # 'bot' | 'admin'

    __table_args__ = (UniqueConstraint('user_id', 'period_start', name='uq_season_pass_period'),)


class PassOffer(Base):
    """Отметка, что предложение купить абонемент уже отправлено.

    Роль та же, что у Registration.last_payment_reminder: защита от повторной рассылки
    при каждом тике фоновой задачи.
    """
    __tablename__ = 'pass_offers'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    period_start = Column(Date, nullable=False)
    sent_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (UniqueConstraint('user_id', 'period_start', name='uq_pass_offer_period'),)

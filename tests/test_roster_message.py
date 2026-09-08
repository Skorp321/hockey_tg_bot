"""Проверка публикации и обновления сообщения со списком.

Настоящий Telegram не нужен: подделываем бота объектом, который записывает вызовы и
умеет бросать те же исключения, что и python-telegram-bot. Именно эти ветки и важны —
потерянное сообщение, лимит частоты, отобранные права.

База — sqlite в памяти: _to_async_url в app/database.py уже разворачивает sqlite://
в sqlite+aiosqlite, а aiosqlite есть в зависимостях.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from telegram.error import BadRequest, Forbidden, RetryAfter

from app.bot import roster_message as rm
from app.models import Base, Player, Registration, Training, TrainingMessage, UserPreferences


class FakeMessage:
    def __init__(self, message_id=555, chat_id=-100123):
        self.message_id = message_id
        self.chat_id = chat_id


class FakeBot:
    """Записывает вызовы; может отдавать заранее заданные исключения."""

    def __init__(self):
        self.sent = []
        self.edited = []
        self.deleted = []
        self.edit_errors = []      # очередь исключений для edit_message_text

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return FakeMessage()

    async def edit_message_text(self, **kwargs):
        if self.edit_errors:
            raise self.edit_errors.pop(0)
        self.edited.append(kwargs)
        return FakeMessage()

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)


@pytest.fixture
async def db(monkeypatch):
    """Чистая база на каждый тест плюс подмена session_scope в модуле."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from contextlib import asynccontextmanager

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_scope():
        async with maker() as session:
            yield session

    monkeypatch.setattr(rm, "session_scope", fake_scope)
    # Дебаунс и пауза между вызовами в тестах не нужны
    monkeypatch.setattr(rm, "MIN_INTERVAL_SECONDS", 0)
    yield maker
    await engine.dispose()


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBot()
    rm.set_roster_bot(fake)
    monkeypatch.setattr(rm.Config, "CHANNEL_ID", "-100123", raising=False)
    monkeypatch.setattr(rm.Config, "MESSAGE_THREAD_ID", None, raising=False)
    yield fake
    rm.set_roster_bot(None)


async def make_training(maker, **kwargs):
    async with maker() as s:
        training = Training(
            date_time=kwargs.get("date_time", datetime.now() + timedelta(days=2)),
            max_participants=20,
            venue="Арена",
        )
        s.add(training)
        await s.flush()
        player = Player(
            user_id=1001, username="alice", display_name="Иванова Алиса",
            goalkeeper=False, is_roster_member=True,
            first_registration=datetime.now(), last_registration=datetime.now(),
            total_registrations=1,
        )
        s.add(player)
        s.add(UserPreferences(user_id=1001, display_name="Иванова Алиса"))
        await s.commit()
        return training.id


async def test_publish_sends_message_and_stores_ids(db, bot):
    training_id = await make_training(db)

    assert await rm.publish_roster(training_id) is True
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == "-100123"

    async with db() as s:
        row = (await s.execute(
            select(TrainingMessage)
        )).scalars().one()
        assert row.message_id == 555
        assert row.text_hash, "хеш текста должен сохраняться"


async def test_second_publish_does_not_duplicate(db, bot):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)
    # Без force повторная публикация не должна слать второе сообщение в канал
    await rm.publish_roster(training_id)
    assert len(bot.sent) == 1


async def test_update_skips_telegram_when_text_unchanged(db, bot):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)

    assert await rm.update_roster_message(training_id) is True
    assert bot.edited == [], "при неизменном тексте запрос к Telegram не нужен"


async def test_update_edits_when_roster_changes(db, bot):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)

    async with db() as s:
        s.add(Registration(training_id=training_id, user_id=1001, username="alice",
                           display_name="Иванова Алиса", registered_at=datetime.now(),
                           goalkeeper=False, paid=False))
        await s.commit()

    assert await rm.update_roster_message(training_id) is True
    assert len(bot.edited) == 1
    assert "✅" in bot.edited[0]["text"], "записавшийся должен получить галочку"


async def test_not_modified_is_treated_as_success(db, bot):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)
    async with db() as s:
        s.add(Registration(training_id=training_id, user_id=1001, username="alice",
                           display_name="Иванова Алиса", registered_at=datetime.now(),
                           goalkeeper=False, paid=False))
        await s.commit()

    bot.edit_errors.append(BadRequest("Message is not modified"))
    assert await rm.update_roster_message(training_id) is True


async def test_lost_message_is_republished(db, bot):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)
    async with db() as s:
        s.add(Registration(training_id=training_id, user_id=1001, username="alice",
                           display_name="Иванова Алиса", registered_at=datetime.now(),
                           goalkeeper=False, paid=False))
        await s.commit()

    bot.edit_errors.append(BadRequest("Message to edit not found"))
    await rm.update_roster_message(training_id)

    assert len(bot.sent) == 2, "потерянное сообщение должно публиковаться заново"
    async with db() as s:
        rows = (await s.execute(select(TrainingMessage))).scalars().all()
        assert len(rows) == 1, "старая строка должна быть заменена, а не продублирована"


async def test_past_training_is_not_republished(db, bot):
    training_id = await make_training(db, date_time=datetime.now() - timedelta(days=1))
    await rm.publish_roster(training_id)
    async with db() as s:
        s.add(Registration(training_id=training_id, user_id=1001, username="alice",
                           display_name="Иванова Алиса", registered_at=datetime.now(),
                           goalkeeper=False, paid=False))
        await s.commit()

    bot.edit_errors.append(BadRequest("Message to edit not found"))
    await rm.update_roster_message(training_id)
    assert len(bot.sent) == 1, "прошедшую тренировку заново публиковать незачем"


async def test_forbidden_disables_further_attempts(db, bot):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)
    async with db() as s:
        s.add(Registration(training_id=training_id, user_id=1001, username="alice",
                           display_name="Иванова Алиса", registered_at=datetime.now(),
                           goalkeeper=False, paid=False))
        await s.commit()

    bot.edit_errors.append(Forbidden("Bot was kicked"))
    assert await rm.update_roster_message(training_id) is False

    async with db() as s:
        row = (await s.execute(select(TrainingMessage))).scalars().one()
        assert row.disabled is True

    # Повторная попытка не должна снова дёргать Telegram
    before = len(bot.edited)
    assert await rm.update_roster_message(training_id) is False
    assert len(bot.edited) == before


async def test_retry_after_is_retried_once(db, bot, monkeypatch):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)
    async with db() as s:
        s.add(Registration(training_id=training_id, user_id=1001, username="alice",
                           display_name="Иванова Алиса", registered_at=datetime.now(),
                           goalkeeper=False, paid=False))
        await s.commit()

    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(rm.asyncio, "sleep", no_sleep)

    bot.edit_errors.append(RetryAfter(1))
    assert await rm.update_roster_message(training_id) is True
    assert len(bot.edited) == 1


async def test_schedule_update_without_bot_is_silent():
    """Сбой публикации не должен ронять запись игрока."""
    rm.set_roster_bot(None)
    rm.schedule_roster_update(123)  # не должно бросить


async def test_delete_removes_message(db, bot):
    training_id = await make_training(db)
    await rm.publish_roster(training_id)
    await rm.delete_roster_message(training_id)
    assert len(bot.deleted) == 1

"""Ответы игроков сохраняются отдельно от мест и отображаются в полном составе."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Training, Player, Registration, TrainingDecline, UserPreferences, JerseyType
from app.roster import build_roster_view, render_roster_text
from app.bot import handlers


@pytest.fixture
async def db(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope():
        async with maker() as session:
            yield session

    monkeypatch.setattr(handlers, 'session_scope', scope)
    monkeypatch.setattr(handlers, 'schedule_roster_update', lambda *args: None)
    async with maker() as s:
        training = Training(date_time=datetime.now()+timedelta(days=2), end_time=time(23), venue='Арена', max_participants=22)
        s.add(training)
        await s.flush()
        for user_id, name, jersey in [(1, 'Первый Игрок', JerseyType.LIGHT), (2, 'Второй Игрок', JerseyType.GREEN)]:
            s.add(Player(user_id=user_id, display_name=name, is_roster_member=True,
                         first_registration=datetime.now(), last_registration=datetime.now()))
            s.add(UserPreferences(user_id=user_id, display_name=name, preferred_jersey_type=jersey))
        await s.commit()
        training_id = training.id
    yield maker, training_id
    await engine.dispose()


def update_for(data, user_id=1):
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(callback_query=SimpleNamespace(data=data, answer=AsyncMock(), message=message),
                           effective_user=SimpleNamespace(id=user_id, username=None, full_name='Первый Игрок'),
                           message=message)


async def text_for(maker, training_id):
    async with maker() as s:
        return render_roster_text(await build_roster_view(s, await s.get(Training, training_id)))


async def test_decline_without_registration_and_repeated_click(db):
    maker, tid = db
    update = update_for(f'decline_{tid}')
    await handlers.decline_training(update, None)
    await handlers.decline_training(update, None)
    async with maker() as s:
        assert len((await s.execute(select(TrainingDecline))).scalars().all()) == 1
        assert not (await s.execute(select(Registration))).scalars().all()
    text = await text_for(maker, tid)
    assert '⬜️Первый Игрок ➖' in text
    assert '🟩Второй Игрок' in text
    assert 'Второй Игрок ✅' not in text


async def test_decline_then_register_then_cancel(db):
    maker, tid = db
    await handlers.decline_training(update_for(f'decline_{tid}'), None)
    await handlers.register_training(update_for(f'register_{tid}'), SimpleNamespace())
    async with maker() as s:
        assert await s.get(TrainingDecline, (tid, 1)) is None
        reg = (await s.execute(select(Registration))).scalars().one()
        assert reg.self_registered
        rid = reg.id
    assert '⬜️Первый Игрок ✅' in await text_for(maker, tid)
    await handlers.cancel_registration(update_for(f'cancel_{rid}'), None)
    assert '⬜️Первый Игрок ➖' in await text_for(maker, tid)
    async with maker() as s:
        assert not (await s.execute(select(Registration))).scalars().all()


async def test_decline_removes_registered_place(db):
    maker, tid = db
    async with maker() as s:
        s.add(Registration(training_id=tid, user_id=1, display_name='Первый Игрок', self_registered=True))
        await s.commit()
    await handlers.decline_training(update_for(f'decline_{tid}'), None)
    assert '⬜️Первый Игрок ➖' in await text_for(maker, tid)
    async with maker() as s:
        assert not (await s.execute(select(Registration))).scalars().all()


async def test_full_roster_for_command_and_button(db):
    _, tid = db
    update = update_for('view_participants')
    await handlers.view_participants(update, None)
    command_text = update.message.reply_text.call_args.args[0]
    assert '⬜️Первый Игрок' in command_text
    assert '🟩Второй Игрок' in command_text
    assert 'Резерв:' in command_text
    buttons = update.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == [f'register_{tid}', f'decline_{tid}']
    update.message.reply_text.reset_mock()
    await handlers.view_training_participants(update, None)
    assert update.message.reply_text.call_args.args[0] == command_text


async def test_old_event_cannot_be_declined(db):
    maker, tid = db
    async with maker() as s:
        training = await s.get(Training, tid)
        training.date_time = datetime.now()-timedelta(days=1)
        await s.commit()
    await handlers.decline_training(update_for(f'decline_{tid}'), None)
    async with maker() as s:
        assert await s.get(TrainingDecline, (tid, 1)) is None


async def test_admin_registration_confirmation_clears_old_decline(db):
    maker, tid = db
    async with maker() as s:
        s.add(Registration(training_id=tid, user_id=1, self_registered=False))
        s.add(TrainingDecline(training_id=tid, user_id=1))
        await s.commit()
    await handlers.register_training(update_for(f'register_{tid}'), SimpleNamespace())
    assert '⬜️Первый Игрок ✅' in await text_for(maker, tid)
    async with maker() as s:
        assert await s.get(TrainingDecline, (tid, 1)) is None


async def test_single_cancel_button_remembers_decline(db):
    maker, tid = db
    async with maker() as s:
        s.add(Registration(training_id=tid, user_id=1, self_registered=True))
        await s.commit()
    await handlers.handle_cancel_registration(update_for('cancel_registration'), None)
    assert '⬜️Первый Игрок ➖' in await text_for(maker, tid)


async def test_declined_guest_stays_visible_in_reserve(db):
    maker, tid = db
    update = update_for(f'decline_{tid}', user_id=3)
    update.effective_user.full_name = 'Гость Третий'
    await handlers.decline_training(update, None)
    text = await text_for(maker, tid)
    assert '1. Гость Третий ➖' in text


def test_participants_available_without_registration():
    for keyboard in (handlers.get_standard_keyboard(), handlers.get_info_keyboard()):
        assert any(button.callback_data == 'view_participants'
                   for row in keyboard.inline_keyboard for button in row)

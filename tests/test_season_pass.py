"""Окно покупки абонемента и автоматическая оплата.

Окно считается от первой тренировки месяца, а не от календарной даты, поэтому
границы и стык месяцев проверяем отдельно — там легче всего ошибиться.
"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.roster import has_season_pass, pass_window, period_start_for
from app.models import Base, SeasonPass, Training


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def add_training(maker, when):
    async with maker() as s:
        s.add(Training(date_time=when, max_participants=20))
        await s.commit()


# Первая тренировка июня — 4-го числа в 21:30
FIRST = datetime(2026, 6, 4, 21, 30)


@pytest.mark.parametrize("now, expected_open", [
    (datetime(2026, 6, 1, 12, 0), False),   # за 3.5 дня — ещё рано
    (datetime(2026, 6, 1, 21, 30), True),   # ровно за 3 дня — уже можно
    (datetime(2026, 6, 3, 10, 0), True),    # внутри окна
    (datetime(2026, 6, 4, 21, 29), True),   # за минуту до начала
    (datetime(2026, 6, 4, 21, 30), False),  # ровно в момент начала — поздно
    (datetime(2026, 6, 10, 10, 0), False),  # после первой тренировки месяца
])
async def test_window_boundaries(maker, now, expected_open):
    await add_training(maker, FIRST)
    await add_training(maker, datetime(2026, 6, 11, 21, 30))  # вторая, на окно не влияет

    async with maker() as s:
        window = await pass_window(s, now=now, open_days_before=3)
    assert (window is not None) is expected_open, f"now={now}"
    if window:
        assert window[0] == date(2026, 6, 1)
        assert window[1] == FIRST


async def test_window_opens_for_next_month_at_month_end(maker):
    """В конце месяца окно должно открыться на первую тренировку СЛЕДУЮЩЕГО месяца."""
    await add_training(maker, datetime(2026, 6, 2, 21, 30))   # первая июньская, уже прошла
    await add_training(maker, datetime(2026, 7, 1, 21, 30))   # первая июльская

    async with maker() as s:
        window = await pass_window(s, now=datetime(2026, 6, 29, 12, 0), open_days_before=3)

    assert window is not None, "за 2 дня до июльской тренировки окно должно быть открыто"
    assert window[0] == date(2026, 7, 1), "период должен быть июльским, а не июньским"


async def test_window_spans_year_boundary(maker):
    """Кандидатом должен становиться январь следующего года, а не месяц 13."""
    await add_training(maker, datetime(2026, 12, 5, 21, 30))  # уже прошла
    await add_training(maker, datetime(2027, 1, 3, 21, 30))

    async with maker() as s:
        # Окно открывается 31.12 в 21:30 — за три дня до тренировки 3 января
        assert await pass_window(
            s, now=datetime(2026, 12, 31, 12, 0), open_days_before=3
        ) is None, "в полдень 31.12 окно ещё не открыто"

        window = await pass_window(s, now=datetime(2026, 12, 31, 22, 0), open_days_before=3)

    assert window is not None
    assert window[0] == date(2027, 1, 1)


async def test_no_trainings_means_no_window(maker):
    async with maker() as s:
        assert await pass_window(s, now=datetime(2026, 6, 1, 12, 0)) is None


async def test_training_on_first_day_of_month(maker):
    """Окно может начинаться в предыдущем месяце — это нормально."""
    await add_training(maker, datetime(2026, 7, 1, 10, 0))

    async with maker() as s:
        window = await pass_window(s, now=datetime(2026, 6, 28, 10, 0), open_days_before=3)
    assert window is not None
    assert window[0] == date(2026, 7, 1)


async def test_has_season_pass(maker):
    period = date(2026, 6, 1)
    async with maker() as s:
        assert await has_season_pass(s, 1001, period) is False
        s.add(SeasonPass(user_id=1001, period_start=period, created_by='bot'))
        await s.commit()
        assert await has_season_pass(s, 1001, period) is True
        # другой месяц не покрывается
        assert await has_season_pass(s, 1001, date(2026, 7, 1)) is False
        # другой игрок тоже
        assert await has_season_pass(s, 1002, period) is False


def test_period_start_for_normalises_to_first_day():
    assert period_start_for(datetime(2026, 6, 17, 23, 59)) == date(2026, 6, 1)
    assert period_start_for(datetime(2026, 1, 1, 0, 0)) == date(2026, 1, 1)


async def test_unique_constraint_blocks_double_purchase(maker):
    """Двойное нажатие не должно создавать два абонемента."""
    from sqlalchemy.exc import IntegrityError

    period = date(2026, 6, 1)
    async with maker() as s:
        s.add(SeasonPass(user_id=1001, period_start=period, created_by='bot'))
        await s.commit()

    async with maker() as s:
        s.add(SeasonPass(user_id=1001, period_start=period, created_by='bot'))
        with pytest.raises(IntegrityError):
            await s.commit()

    async with maker() as s:
        rows = (await s.execute(
            select(SeasonPass).where(SeasonPass.user_id == 1001)
        )).scalars().all()
        assert len(rows) == 1


async def test_window_respects_custom_open_days(maker):
    await add_training(maker, FIRST)
    async with maker() as s:
        # за 5 дней при настройке 3 — закрыто
        assert await pass_window(s, now=FIRST - timedelta(days=5), open_days_before=3) is None
        # та же дата при настройке 7 — открыто
        assert await pass_window(s, now=FIRST - timedelta(days=5), open_days_before=7) is not None

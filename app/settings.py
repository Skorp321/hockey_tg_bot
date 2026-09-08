"""Настройки, которые нужно менять без передеплоя.

Живут в таблице app_settings. Она приезжает на прод сама через create_all, поэтому
добавление новой настройки не требует SQL-миграции — в отличие от новой колонки.

Значения по умолчанию заданы здесь: пока строки в таблице нет, действует дефолт,
и заводить её заранее не нужно.
"""

from sqlalchemy import select

from .models import AppSetting

DEFAULTS = {
    # Шапка списка состава
    "roster.default_venue": "",
    "roster.default_deadline_text": "",
    "roster.default_end_time": "",  # «23:00»; пусто — не показывать время окончания
    # Сколько пустых строк оставлять в блоке «Резерв»
    "roster.reserve_slots": "3",
    # Раньше в коде было жёстко 2, но в реальном списке вратарей четверо
    "roster.max_goalkeepers": "6",
    # За сколько дней до первой тренировки месяца показывать кнопку абонемента
    "pass.open_days_before": "3",
}


async def get_settings(session) -> dict:
    """Все настройки одним запросом: дефолты, перекрытые тем, что есть в базе."""
    values = dict(DEFAULTS)
    rows = (await session.execute(select(AppSetting))).scalars().all()
    for row in rows:
        if row.value is not None:
            values[row.key] = row.value
    return values


async def get_setting(session, key: str, default=None):
    row = (await session.execute(
        select(AppSetting).where(AppSetting.key == key)
    )).scalars().first()
    if row is not None and row.value is not None:
        return row.value
    if default is not None:
        return default
    return DEFAULTS.get(key)


async def set_setting(session, key: str, value) -> None:
    row = (await session.execute(
        select(AppSetting).where(AppSetting.key == key)
    )).scalars().first()
    if row is None:
        session.add(AppSetting(key=key, value=str(value)))
    else:
        row.value = str(value)


def as_int(values: dict, key: str) -> int:
    """Настройки хранятся строками; битое значение не должно ронять страницу."""
    try:
        return int(values.get(key, DEFAULTS.get(key, 0)))
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key, 0))

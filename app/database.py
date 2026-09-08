import logging
import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import Config
from .models import Base

logger = logging.getLogger(__name__)


def _to_async_url(url: str) -> str:
    """Приводит DATABASE_URL к async-драйверу.

    Нужно, чтобы существующие .env и docker-compose.yml продолжали работать
    без правок: там указан обычный postgresql:// (psycopg2).
    """
    if "+asyncpg" in url or "+aiosqlite" in url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgresql+psycopg2://") or url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.split("://", 1)[1]
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url.split("://", 1)[1]
    return url


DATABASE_URL = _to_async_url(Config.SQLALCHEMY_DATABASE_URI)

# У SQLite используется NullPool, который не принимает pool_size/max_overflow,
# поэтому параметры пула задаём только для реальных серверных БД.
_engine_kwargs = {"pool_pre_ping": True}
if not DATABASE_URL.startswith("sqlite"):
    _engine_kwargs.update(pool_size=5, max_overflow=10)

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)

# expire_on_commit=False обязателен: иначе после commit() любое чтение атрибута
# ORM-объекта уходит в ленивую подгрузку и падает с MissingGreenlet.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db():
    """Зависимость FastAPI: одна AsyncSession на запрос."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope():
    """Для хендлеров бота и фоновых задач: `async with session_scope() as session:`"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# Заполняется verify_enum_schema(). Не None означает, что схема БД отстала от кода;
# /health в этом случае отдаёт 503, и деплой падает на health-check, а не через несколько
# часов первым же «invalid input value for enum».
schema_stale_reason = None


async def verify_enum_schema():
    """Сверяет значения enum в БД с перечислениями в коде.

    create_all(checkfirst=True) создаёт отсутствующие таблицы, но не меняет уже
    существующие типы, поэтому забытая миграция иначе никак себя не проявит до первой
    записи нового значения.
    """
    global schema_stale_reason
    schema_stale_reason = None

    if not DATABASE_URL.startswith("postgresql"):
        return  # у sqlite enum эмулируется через varchar, сверять нечего

    from sqlalchemy import text
    from .models import JerseyType, PositionType

    expected = {
        "jerseytype": {item.value for item in JerseyType},
        "positiontype": {item.value for item in PositionType},
    }

    problems = []
    try:
        async with engine.connect() as conn:
            for type_name, wanted in expected.items():
                rows = await conn.execute(text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :name"
                ), {"name": type_name})
                present = {row[0] for row in rows}
                if not present:
                    # Нативного типа нет — значит колонка varchar и принимает что угодно.
                    continue
                missing = wanted - present
                if missing:
                    problems.append(f"{type_name}: в БД нет значений {sorted(missing)}")
    except Exception as exc:  # недоступная БД — не повод падать здесь
        logger.warning(f"Не удалось сверить схему enum: {exc}")
        return

    if problems:
        schema_stale_reason = "; ".join(problems)
        logger.error(
            f"❌ Схема БД отстала от кода: {schema_stale_reason}. "
            f"Примените миграции: bash scripts/run-migrations.sh"
        )
        if os.getenv("ALLOW_STALE_SCHEMA") == "1":
            logger.warning("⚠️ ALLOW_STALE_SCHEMA=1 — запускаемся несмотря на расхождение")
            schema_stale_reason = None


async def init_models():
    """Создаёт таблицы. Вызывается из run.py до старта бота."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await verify_enum_schema()

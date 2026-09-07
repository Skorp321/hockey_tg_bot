from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import Config
from .models import Base


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


async def init_models():
    """Создаёт таблицы. Вызывается из run.py до старта бота."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

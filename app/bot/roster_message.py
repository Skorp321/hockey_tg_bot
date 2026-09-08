"""Публикация списка состава в Telegram и его обновление на месте.

Это первое место в проекте, где бот редактирует уже отправленное сообщение: везде
остальное он только шлёт новые через reply_text.

Три уровня защиты от лимитов Telegram, потому что на волне записей события идут пачкой:

1. Дебаунс: schedule_roster_update откладывает работу на несколько секунд и отменяет
   предыдущую отложенную задачу для той же тренировки. Десять записей подряд дают
   одно редактирование.
2. Блокировка на тренировку плюс минимальный интервал между обращениями к Telegram.
3. Сравнение хеша текста: если текст не изменился, запроса к Telegram не будет вовсе.
   Это же снимает все ошибки «message is not modified» — например, когда админ жмёт
   «Запомнить», а видимая часть списка не меняется.

schedule_roster_update намеренно синхронная и никогда не бросает: сбой публикации
не должен ронять запись игрока на тренировку.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime

from sqlalchemy import select
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from ..config import Config
from ..database import session_scope
from ..models import Training, TrainingMessage
from ..roster import build_roster_view, render_roster_text
from ..settings import get_settings

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 3.0
# Telegram ограничивает частоту сообщений в один чат; держим паузу с запасом.
MIN_INTERVAL_SECONDS = 1.2

_bot = None
_pending = {}   # training_id -> asyncio.Task
_locks = {}     # training_id -> asyncio.Lock
_throttle_lock = None
_registry_loop = None
_last_call_at = 0.0


def _ensure_registry():
    """Возвращает реестр блокировок, привязанный к текущему event loop.

    asyncio.Lock запоминает loop, в котором её впервые взяли, и в другом loop
    зависает намертво. В проде loop один на весь процесс, но при смене (перезапуск
    внутри процесса, тесты) обновления списков молча вставали бы навсегда.
    """
    global _registry_loop, _throttle_lock
    loop = asyncio.get_running_loop()
    if _registry_loop is not loop:
        _locks.clear()
        _pending.clear()
        _throttle_lock = asyncio.Lock()
        _registry_loop = loop
    return _locks


def set_roster_bot(bot) -> None:
    """Вызывается из run.py после успешного старта бота."""
    global _bot
    _bot = bot


def get_roster_bot():
    return _bot


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _throttle() -> None:
    global _last_call_at
    _ensure_registry()
    async with _throttle_lock:
        delay = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call_at)
        if delay > 0:
            await asyncio.sleep(delay)
        _last_call_at = time.monotonic()


async def _render(session, training) -> tuple:
    settings = await get_settings(session)
    view = await build_roster_view(session, training, settings)
    return render_roster_text(view), view


# --- Публикация ------------------------------------------------------------------

async def publish_roster(training_id: int, force: bool = False) -> bool:
    """Публикует список первый раз (или заново, если сообщение потеряно)."""
    if _bot is None:
        logger.warning("Список не опубликован: бот не запущен")
        return False
    if not Config.CHANNEL_ID:
        logger.warning("Список не опубликован: CHANNEL_ID не настроен")
        return False

    try:
        async with session_scope() as session:
            training = await session.get(Training, training_id)
            if training is None:
                return False

            existing = (await session.execute(
                select(TrainingMessage).where(TrainingMessage.training_id == training_id)
            )).scalars().first()
            if existing is not None and not force:
                # Уже опубликовано — не плодим второе сообщение, просто обновим.
                schedule_roster_update(training_id, delay=0)
                return True

            text, _view = await _render(session, training)

            await _throttle()
            params = {"chat_id": Config.CHANNEL_ID, "text": text}
            if Config.MESSAGE_THREAD_ID:
                params["message_thread_id"] = int(Config.MESSAGE_THREAD_ID)
            message = await _bot.send_message(**params)

            if existing is not None:
                await session.delete(existing)
                await session.flush()

            session.add(TrainingMessage(
                training_id=training_id,
                chat_id=message.chat_id,
                message_id=message.message_id,
                thread_id=int(Config.MESSAGE_THREAD_ID) if Config.MESSAGE_THREAD_ID else None,
                text_hash=_digest(text),
                last_edit_at=datetime.now(),
            ))
            await session.commit()
            logger.info(f"✅ Список тренировки {training_id} опубликован")
            return True
    except Exception as exc:
        logger.error(f"❌ Не удалось опубликовать список тренировки {training_id}: {exc}")
        return False


async def delete_roster_message(training_id: int) -> None:
    """Убирает сообщение при удалении тренировки. Ошибки не важны."""
    if _bot is None:
        return
    try:
        async with session_scope() as session:
            rows = (await session.execute(
                select(TrainingMessage).where(TrainingMessage.training_id == training_id)
            )).scalars().all()
            for row in rows:
                try:
                    await _throttle()
                    await _bot.delete_message(chat_id=row.chat_id, message_id=row.message_id)
                except Exception as exc:
                    logger.info(f"Сообщение списка {training_id} не удалено: {exc}")
    except Exception as exc:
        logger.error(f"Ошибка при удалении сообщения списка {training_id}: {exc}")


# --- Обновление ------------------------------------------------------------------

def schedule_roster_update(training_id: int, delay: float = DEBOUNCE_SECONDS) -> None:
    """Ставит отложенное обновление. Синхронная, ничего не бросает.

    Вызывается из обработчиков записи, оплаты и правок в панели — им не должно быть
    дела до того, работает ли Telegram.
    """
    if _bot is None or not training_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # вне event loop (например, в тестах) — молча ничего не делаем

    _ensure_registry()
    previous = _pending.get(training_id)
    if previous is not None and not previous.done():
        previous.cancel()
    _pending[training_id] = loop.create_task(_delayed_update(training_id, delay))


async def _delayed_update(training_id: int, delay: float) -> None:
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        await update_roster_message(training_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"❌ Ошибка обновления списка тренировки {training_id}: {exc}")
    finally:
        # Снимаем только свою задачу: за время работы могла появиться новая.
        current = asyncio.current_task()
        if _pending.get(training_id) is current:
            _pending.pop(training_id, None)


async def update_roster_message(training_id: int) -> bool:
    """Перерисовывает опубликованное сообщение, если текст изменился."""
    if _bot is None:
        return False

    lock = _ensure_registry().setdefault(training_id, asyncio.Lock())
    async with lock:
        async with session_scope() as session:
            training = await session.get(Training, training_id)
            if training is None:
                return False

            row = (await session.execute(
                select(TrainingMessage).where(TrainingMessage.training_id == training_id)
            )).scalars().first()
            if row is None or row.disabled:
                return False

            text, _view = await _render(session, training)
            digest = _digest(text)
            if row.text_hash == digest:
                return True  # ничего не изменилось — Telegram не дёргаем вовсе

            ok = await _edit(session, row, training, text)
            if ok:
                row.text_hash = digest
                row.last_edit_at = datetime.now()
            await session.commit()
            return ok


async def _edit(session, row, training, text) -> bool:
    """Одна попытка редактирования с разбором ошибок Telegram."""
    try:
        await _throttle()
        await _bot.edit_message_text(
            chat_id=row.chat_id, message_id=row.message_id, text=text,
        )
        return True

    except RetryAfter as exc:
        logger.warning(f"Telegram просит подождать {exc.retry_after}с, повторяем один раз")
        try:
            await asyncio.sleep(float(exc.retry_after) + 1)
            await _bot.edit_message_text(
                chat_id=row.chat_id, message_id=row.message_id, text=text,
            )
            return True
        except Exception as retry_exc:
            logger.error(f"Повтор после RetryAfter не удался: {retry_exc}")
            return False

    except BadRequest as exc:
        message = str(exc).lower()
        if "not modified" in message:
            # Текст совпал с тем, что уже в канале — считаем успехом и запоминаем хеш.
            return True
        if "message to edit not found" in message or "message_id_invalid" in message:
            logger.warning(
                f"Сообщение списка тренировки {training.id} потеряно, публикуем заново"
            )
            await session.delete(row)
            await session.commit()
            if training.date_time > datetime.now():
                await publish_roster(training.id, force=True)
            return False
        logger.error(f"Некорректный запрос при обновлении списка: {exc}")
        return False

    except Forbidden as exc:
        # Бота выгнали или лишили прав: перестаём долбиться, но строку сохраняем,
        # чтобы админ увидел причину и мог опубликовать заново.
        logger.error(f"Нет доступа к чату для списка тренировки {training.id}: {exc}")
        row.disabled = True
        return False

    except (NetworkError, TimedOut) as exc:
        logger.warning(f"Сеть недоступна при обновлении списка: {exc}")
        return False

    except Exception as exc:
        logger.error(f"Неожиданная ошибка при обновлении списка: {exc}")
        return False


# --- Восстановление после перезапуска --------------------------------------------

async def reconcile_rosters() -> int:
    """Обновляет списки будущих тренировок после старта бота.

    Отложенные задачи живут в процессе, поэтому рестарт мог потерять правку.
    Публикацию для тренировок БЕЗ сообщения здесь намеренно не делаем: иначе
    неудачный деплой приводил бы к дублям в канале.
    """
    if _bot is None:
        return 0
    try:
        async with session_scope() as session:
            rows = (await session.execute(
                select(TrainingMessage)
                .join(Training, Training.id == TrainingMessage.training_id)
                .where(Training.date_time > datetime.now())
                .where(TrainingMessage.disabled.is_(False))
            )).scalars().all()
            training_ids = [row.training_id for row in rows]

        for training_id in training_ids:
            schedule_roster_update(training_id, delay=0)
        if training_ids:
            logger.info(f"🔄 Запланировано обновление списков: {len(training_ids)}")
        return len(training_ids)
    except Exception as exc:
        logger.error(f"Ошибка сверки списков после старта: {exc}")
        return 0

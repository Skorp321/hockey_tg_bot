import asyncio
import signal
from app import create_app
from app.database import init_models
from app.bot.handlers import (
    start_bot, check_payment_reminders, check_season_pass_offers,
)
from app.bot.roster_message import set_roster_bot, reconcile_rosters
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig

async def shutdown(signal, loop, bot_app):
    """Корректное завершение приложения"""
    print(f"Received exit signal {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    
    # Останавливаем бота
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    
    # Отменяем все задачи
    [task.cancel() for task in tasks]
    print("Waiting for tasks to complete...")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

async def main():
    # Получаем текущий event loop
    loop = asyncio.get_event_loop()
    
    # Создаем FastAPI приложение
    app = create_app()

    # Создаём таблицы до старта бота: раньше это делалось внутри create_app(),
    # а lifespan сработал бы только при serve(), то есть уже после запуска бота.
    await init_models()

    config = HyperConfig()
    config.bind = ["0.0.0.0:5000"]
    config.use_reloader = False
    
    bot_app = None
    
    # Запускаем бота с повторными попытками
    max_retries = 5
    retry_delay = 10  # секунд
    
    for attempt in range(max_retries):
        try:
            print(f"🔄 Попытка запуска бота {attempt + 1}/{max_retries}")
            bot_app = await start_bot()
            break
        except Exception as e:
            print(f"❌ Ошибка при запуске бота (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                print(f"⏳ Повторная попытка через {retry_delay} секунд...")
                await asyncio.sleep(retry_delay)
            else:
                print("❌ Не удалось запустить бота после всех попыток")
                # Продолжаем работу только с веб-сервером
                bot_app = None

    # Отдаём бота веб-слою: /send-weekly-post использует уже запущенный экземпляр
    app.state.bot_app = bot_app
    app.state.bot = bot_app.bot if bot_app else None

    # Тот же экземпляр нужен модулю списков состава: он редактирует опубликованное
    # сообщение при каждой записи и оплате.
    if bot_app:
        set_roster_bot(bot_app.bot)
        # Отложенные обновления живут в процессе, поэтому рестарт мог потерять правку.
        # Досылаем их для будущих тренировок, у которых сообщение уже опубликовано.
        await reconcile_rosters()

    # Запускаем фоновую задачу для проверки напоминаний об оплате
    async def payment_reminder_task():
        """Фоновая задача для проверки напоминаний об оплате"""
        while True:
            try:
                if bot_app and bot_app.bot:
                    await check_payment_reminders(bot_app.bot)
                    # Второй цикл не заводим: предложения абонемента проверяются
                    # тем же тиком, 30 минут для окна в три дня более чем достаточно.
                    await check_season_pass_offers(bot_app.bot)
                else:
                    print("⚠️ Бот не запущен, пропускаем проверку напоминаний")
            except Exception as e:
                print(f"❌ Ошибка в фоновой задаче напоминаний: {e}")
            
            # Ждем 30 минут до следующей проверки
            await asyncio.sleep(30 * 60)
    
    # Запускаем фоновую задачу
    if bot_app:
        asyncio.create_task(payment_reminder_task())
        print("🔄 Запущена фоновая задача проверки напоминаний об оплате")
        
        # Планировщик сообщений уже запущен в start_bot через start_message_scheduler
    
    # Добавляем обработчики сигналов для корректного завершения
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(shutdown(s, loop, bot_app))
        )
    
    try:
        # Запускаем веб-сервер
        print("🌐 Запуск веб-сервера...")
        await serve(app, config)
    except Exception as e:
        print(f"❌ Ошибка веб-сервера: {e}")
        if bot_app:
            await bot_app.stop()
            await bot_app.shutdown()
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"\nError occurred: {e}") 
import asyncio
import signal
from app import create_app
from app.bot.handlers import start_bot
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
    
    # Создаем Flask приложение
    app = create_app()
    config = HyperConfig()
    config.bind = ["0.0.0.0:5000"]
    config.use_reloader = False
    
    # Запускаем бота
    try:
        bot_app = await start_bot()
        
        # Добавляем обработчики сигналов для корректного завершения
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(shutdown(s, loop, bot_app))
            )
        
        # Запускаем веб-сервер
        await serve(app, config)
    except Exception as e:
        print(f"Error occurred: {e}")
        if 'bot_app' in locals():
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
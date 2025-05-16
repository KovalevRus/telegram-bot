import os
import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.getenv("PORT", 8000))
WEBHOOK_PATH = "/webhook"
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is not set in environment variables")
    exit(1)

async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Получено сообщение: {update.message.text}")
    # Пример ответа
    await update.message.reply_text(f"Вы написали: {update.message.text}")

async def webhook_handler(request: web.Request):
    data = await request.json()
    telegram_app = request.app['telegram_app']
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.update_queue.put(update)
    logger.info(f"Получен апдейт на /webhook: {data}")
    return web.Response(text="OK")

async def health_handler(request: web.Request):
    return web.Response(text="OK")

async def async_main():
    # Создаем Telegram приложение
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_update))

    # Создаем aiohttp приложение
    app = web.Application()
    app['telegram_app'] = telegram_app

    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/", health_handler)

    # Устанавливаем webhook
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
        logger.info(f"Устанавливаем webhook: {webhook_url}")
        await telegram_app.bot.set_webhook(webhook_url)
    else:
        logger.warning("RENDER_EXTERNAL_URL не задан, webhook не будет установлен")

    # Инициализируем и запускаем Telegram приложение
    await telegram_app.initialize()
    await telegram_app.start()

    # Запускаем aiohttp сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=PORT)
    await site.start()

    logger.info(f"Webhook сервер запущен на порту {PORT}")

    try:
        while True:
            await asyncio.sleep(3600)  # Просто спим, чтобы не завершать работу
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение работы сервера")

    await telegram_app.stop()
    await telegram_app.shutdown()
    await runner.cleanup()

def main():
    asyncio.run(async_main())

if __name__ == "__main__":
    main()

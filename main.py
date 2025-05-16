import os
import logging
import asyncio
from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

import aiohttp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_PATH = "/webhook"

if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
    logger.error("TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY is not set in environment variables")
    exit(1)


async def query_openrouter_with_retry(payload, headers, retries=2):
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    result = await response.json()

                    # Проверяем есть ли ошибка в ответе модели
                    if result.get("choices") and result["choices"][0].get("error"):
                        logger.warning("Ошибка OpenRouter: %s", result["choices"][0]["error"])
                        await asyncio.sleep(1)
                        continue

                    return result
        except Exception as e:
            logger.warning("Ошибка запроса к OpenRouter: %s", e)
            await asyncio.sleep(1)

    # Возврат fallback-ответа при ошибках
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Извините, сейчас возникли технические трудности, пожалуйста, повторите запрос позже."
            }
        }]
    }


async def ask_model(question: str):
    payload = {
        "model": "deepseek/deepseek-r1:free",
        "messages": [{"role": "user", "content": question}],
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    response = await query_openrouter_with_retry(payload, headers)

    content = response["choices"][0]["message"]["content"]
    if not content.strip():
        logger.warning("Модель вернула пустой ответ. Full raw response: %s", response)
        return "Ответ пуст. Пожалуйста, повторите вопрос."
    return content


async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Получен апдейт: {update.to_dict()}")

    if update.message and update.message.text:
        user_text = update.message.text
        logger.info(f"Запрос от пользователя: {user_text}")

        answer = await ask_model(user_text)
        await update.message.reply_text(answer)
    else:
        logger.info("Получено сообщение без текста")


async def handle_health(request):
    return web.Response(text="OK")


async def on_startup(app):
    logger.info("Webhook сервер запущен")


def main():
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    telegram_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_update))

    # Aiohttp web app
    app = web.Application()

    # Webhook endpoint для Telegram
    async def webhook_handler(request):
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.update_queue.put(update)
        return web.Response(text="OK")

    app.router.add_post(WEBHOOK_PATH, webhook_handler)

    # Для проверки здоровья сервиса (health check)
    async def health_handler(request):
        return web.Response(text="OK")

    app.router.add_get("/", health_handler)

    # Запускаем webhook для telegram
    # Устанавливаем webhook
    webhook_url = os.getenv("RENDER_EXTERNAL_URL")
    if webhook_url:
        full_webhook_url = f"{webhook_url}{WEBHOOK_PATH}"
        logger.info(f"Setting webhook URL to: {full_webhook_url}")
        asyncio.run(telegram_app.bot.set_webhook(full_webhook_url))
    else:
        logger.warning("RENDER_EXTERNAL_URL is not set, webhook won't be set automatically")

    # Запускаем Telegram app и aiohttp сервер одновременно
    loop = asyncio.get_event_loop()

    # Запускаем Telegram приложение (оно работает в фоне)
    loop.create_task(telegram_app.initialize())
    loop.create_task(telegram_app.start())
    loop.create_task(telegram_app.updater.start_polling())  # Подстраховка, на случай если webhook не работает

    # Запускаем aiohttp сервер
    web.run_app(app, port=PORT)


if __name__ == "__main__":
    main()

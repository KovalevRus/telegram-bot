import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
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
    logger.error("TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY is not set")
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

                    if result.get("choices") and result["choices"][0].get("error"):
                        logger.warning("OpenRouter error: %s", result["choices"][0]["error"])
                        await asyncio.sleep(1)
                        continue

                    return result
        except Exception as e:
            logger.warning("Request error to OpenRouter: %s", e)
            await asyncio.sleep(1)

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

    message = update.message
    if not message or not message.text:
        logger.info("Получено сообщение без текста")
        return

    user_text = message.text
    chat_type = message.chat.type

    # В группах — только при упоминании или reply боту
    if chat_type in ("group", "supergroup"):
        bot_username = (await context.bot.get_me()).username

        mentioned = f"@{bot_username}" in user_text
        is_reply_to_bot = (
            message.reply_to_message and
            message.reply_to_message.from_user and
            message.reply_to_message.from_user.id == context.bot.id
        )

        if not mentioned and not is_reply_to_bot:
            logger.info("Сообщение в группе проигнорировано (нет упоминания или ответа)")
            return

    logger.info(f"Запрос от пользователя: {user_text}")
    answer = await ask_model(user_text)
    await message.reply_text(answer)


async def handle_health(request):
    return web.Response(text="OK")


async def main():
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_update))

    app = web.Application()

    async def webhook_handler(request):
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.update_queue.put(update)
        return web.Response(text="OK")

    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/", handle_health)

    webhook_url = os.getenv("RENDER_EXTERNAL_URL")
    if webhook_url:
        full_webhook_url = f"{webhook_url}{WEBHOOK_PATH}"
        logger.info(f"Устанавливаем webhook: {full_webhook_url}")
        await telegram_app.bot.set_webhook(full_webhook_url)
    else:
        logger.warning("RENDER_EXTERNAL_URL не задан — webhook не установлен")

    await telegram_app.initialize()
    await telegram_app.start()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Сервер aiohttp запущен на порту {PORT}")

    # Не даём выйти из функции — ждём завершения
    await telegram_app.updater.wait_until_shutdown()
    await telegram_app.stop()
    await telegram_app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())

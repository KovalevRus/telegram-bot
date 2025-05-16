import os
import logging
import asyncio
from aiohttp import web
from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import aiohttp
import re

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

# --- Markdown → HTML конвертер ---
def markdown_to_html(text: str) -> str:
    # Экранируем HTML
    text = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

    # **bold** → <b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # *italic* → <i>
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # `code` → <code>
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # ### Заголовки → <b> (Markdown-style заголовки)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Ссылки [text](url) → <a href="url">text</a>
    text = re.sub(r"\[([^\]]+)]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    return text

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
                    return result
        except Exception as e:
            logger.warning("Ошибка запроса к OpenRouter: %s", e)
            await asyncio.sleep(1)

    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Извините, возникли технические трудности. Повторите запрос позже."
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

    return markdown_to_html(content)

async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    # Обработка только упоминаний или ответов
    if not message or not message.text:
        return
    if not (message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id) and not message.entities:
        return

    # Проверка на упоминание бота
    mentioned = any(
        e.type == "mention" or e.type == "text_mention"
        for e in message.entities
    )

    if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id or mentioned:
        user_text = message.text
        logger.info(f"Запрос от пользователя: {user_text}")

        answer = await ask_model(user_text)
        await message.reply_text(answer, parse_mode=ParseMode.HTML)

async def handle_health(request):
    return web.Response(text="OK")

async def run():
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
        logger.info(f"Setting webhook URL to: {full_webhook_url}")
        await telegram_app.bot.set_webhook(full_webhook_url)
    else:
        logger.warning("RENDER_EXTERNAL_URL is not set, webhook won't be set automatically")

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=PORT)
    await site.start()

    logger.info("Бот и сервер запущены. Ожидание событий...")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(run())

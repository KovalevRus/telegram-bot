import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import aiohttp
import re
import json

# === ЛОГИ ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# === КОНФИГ ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_PATH = "/webhook"
HISTORY_FILE = "chat_histories.json"
MAX_TOKENS = 600  # Ограничение на количество токенов в ответе

if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
    logger.error("TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY is not set in environment variables")
    exit(1)

# === ИСТОРИЯ ===
chat_histories = {}

def load_chat_histories():
    global chat_histories
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            chat_histories = json.load(f)
            logger.info("История загружена")

def save_chat_histories():
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(chat_histories, f, ensure_ascii=False, indent=2)

def append_to_history(chat_id: str, role: str, content: str, max_messages=20):
    history = chat_histories.get(chat_id, [])
    history.append({"role": role, "content": content})
    chat_histories[chat_id] = history[-max_messages:]
    save_chat_histories()

# === Markdown → HTML ===
def markdown_to_html(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text

# === Запрос к OpenRouter ===
async def query_openrouter(payload, headers, retries=2):
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload) as response:
                    return await response.json()
        except Exception as e:
            logger.warning("Ошибка запроса: %s", e)
            await asyncio.sleep(1)

    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": ""
            }
        }]
    }

# === Основной запрос к ИИ ===
async def ask_model(chat_id: str, user_text: str) -> str:
    append_to_history(chat_id, "user", user_text)

    payload = {
        "model": "deepseek/deepseek-r1:free",
        "messages": chat_histories.get(chat_id, []),
        "max_tokens": MAX_TOKENS  # Добавлено ограничение токенов
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    response = await query_openrouter(payload, headers)
    content = response["choices"][0]["message"]["content"]

    if not content.strip():
        logger.warning("Модель вернула пустой ответ. Full raw response: %s", response)
        return "Ответ пуст. Пожалуйста, повторите вопрос."

    append_to_history(chat_id, "assistant", content)
    return markdown_to_html(content)

# === Обработка входящих сообщений ===
async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id
    mentioned = any(e.type in {"mention", "text_mention"} for e in message.entities or [])

    if is_reply_to_bot or mentioned:
        user_text = message.text
        chat_id = str(message.chat_id)

        logger.info(f"[{chat_id}] Запрос: {user_text}")
        answer = await ask_model(chat_id, user_text)
        await message.reply_text(answer, parse_mode=ParseMode.HTML)

# === Хелсчек ===
async def handle_health(request):
    return web.Response(text="OK")

# === Запуск бота ===
async def run():
    load_chat_histories()

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
        logger.info(f"Установка вебхука: {full_webhook_url}")
        await telegram_app.bot.set_webhook(full_webhook_url)

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=PORT)
    await site.start()

    logger.info("Бот запущен")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(run())

import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown
import aiohttp
import re
import json
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

# === ЛОГИ ===
logging.basicConfig(
    format=' %(levelname)-8s %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger()


# === КОНФИГ ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_PATH = "/webhook"

if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
    logger.error("TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY is not set in environment variables")
    exit(1)


# === Firebase (Admin) ===
def initialize_firebase():
    firebase_key = os.getenv("FIREBASE_CREDENTIALS_JSON")
    if not firebase_key:
        logger.error("Environment variable FIREBASE_KEY is missing")
        exit(1)

    cred = credentials.Certificate(json.loads(firebase_key))
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    return db


db = initialize_firebase()


# === ИСТОРИЯ ЧАТОВ ===
def load_chat_history(chat_id: str):
    doc = db.collection("chat_histories").document(chat_id).get()
    return doc.to_dict().get("messages", []) if doc.exists else []


def save_chat_history(chat_id: str, history):
    db.collection("chat_histories").document(chat_id).set({"messages": history})


def append_to_history(chat_id: str, role: str, content: str, max_tokens=4096):
    history = load_chat_history(chat_id)
    new_entry = {"role": role, "content": content}
    history.append(new_entry)

    # Обрезка истории по количеству токенов (примерно)
    def count_tokens(msgs):
        return sum(len(m["content"]) // 4 + 4 for m in msgs)

    while count_tokens(history) > max_tokens:
        history.pop(0)

    save_chat_history(chat_id, history)


# === Запрос к OpenRouter с логированием ===
async def query_openrouter(payload, headers, retries=2):
    url = "https://openrouter.ai/api/v1/chat/completions"

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    status = response.status
                    text = await response.text()

                    if status != 200:
                        logger.warning(f"OpenRouter ответил с кодом {status}: {text}")

                    logger.debug(f"Успешный ответ: {text}")

                    response_json = json.loads(text)

                    if status == 429:
                        logger.warning("Превышен лимит.")
                        reset_raw = response_json.get("error", {}).get("metadata", {}).get("headers", {}).get("X-RateLimit-Reset")
                        return {
                            "choices": [{"message": {"role": "assistant", "content": ""}}],
                            "rate_limit_reset": reset_raw
                        }

                    return response_json
        except Exception as e:
            logger.error(f"Ошибка запроса к OpenRouter (попытка {attempt + 1}): {e}")
            await asyncio.sleep(1)

    logger.error("Все попытки завершились с ошибкой.")
    return {"choices": [{"message": {"role": "assistant", "content": ""}}]}

# === Основной процесс с моделями ===
async def ask_model(chat_id: str, user_text: str) -> str:
    append_to_history(chat_id, "user", user_text)
    history = load_chat_history(chat_id)

    models = [
        ("DeepSeek", "deepseek/deepseek-chat-v3-0324:free"),
        ("DeepSeek", "deepseek/deepseek-r1:free"),
        ("Gemini", "google/gemini-2.5-pro-exp-03-25"),
        ("LLaMA", "meta-llama/llama-4-maverick:free"),
        ("Qwen", "qwen/qwen3-235b-a22b:free"),
        ("Microsoft", "microsoft/mai-ds-r1:free"),
        ("Gemma", "google/gemma-3-27b-it:free"),
    ]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    for model_label, model_name in models:
        logger.info(f"Попытка с помощью модели {model_label}")

        payload = {
            "model": model_name,
            "messages": history,
            "max_tokens": 1024
        }

        response = await query_openrouter(payload, headers)

        if not response:
            logger.warning(f"{model_label} — нет ответа.")
            continue

        logger.debug(f"Ответ модели {model_label}: {json.dumps(response, ensure_ascii=False, indent=2)}")

        try:
            message = response.get("choices", [{}])[0].get("message", {})
            content = message.get("content", "").strip()
        except Exception as e:
            logger.error(f"Не получилось извлечь текст из ответа: {e}")
            continue

        if content:
            logger.info(f"{model_label} выдал ответ.")
            # Запомним ответ в истории
            append_to_history(chat_id, "assistant", content)
            return content

        reset_raw = response.get("rate_limit_reset")
        if reset_raw:
            try:
                reset_timestamp = datetime.utcfromtimestamp(int(reset_raw) / 1000)
                msk = timezone(timedelta(hours=3))
                reset_time_msk = reset_timestamp.replace(tzinfo=timezone.utc).astimezone(msk)
                reset_time_str = reset_time_msk.strftime("%Y-%m-%d %H:%M:%S")
            except Exception as e:
                logger.error(f"Не получилось преобразовать время лимита: {e}")
                reset_time_str = "неизвестно"
        else:
            reset_time_str = "неизвестно"

        logger.warning(f"{model_label} — лимит исчерпан.")
        return f"🚫 Превышен лимит, следующий лимит сработает в {reset_time_str}"

    return "Извините, модели сейчас не доступны."


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
        logger.info(f"[{chat_id}] Ответ: {answer}")

        # Экранирование MarkdownV2
        answer_escaped = escape_markdown(answer, version=2)

        await message.reply_text(answer_escaped, parse_mode='MarkdownV2') 


# === Хелсчек ===
async def handle_health(request):
    return web.Response(text='OK')

# === Запуск бота ===
async def run():
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_update))

    app = web.Application()

    async def webhook_handler(request):
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.update_queue.put(update)
        return web.Response(text='OK')

    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/", handle_health)

    webhook_url = os.getenv("RENDER_EXTERNAL_URL")
    if webhook_url:
        full_webhook_url = f"{webhook_url}{WEBHOOK_PATH}"
        logger.info(f"Установка вебхука: {full_webhook_url}")
        await telegram_app.bot.set_webhook(full_webhook_url)

    await telegram_app.initialize()
    await telegram_app.start()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=PORT)
    await site.start()

    logger.info("Бот запущен")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(run())  

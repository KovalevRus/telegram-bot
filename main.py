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
from firebase_config_loader import initialize_firebase
from dotenv import load_dotenv

load_dotenv()

# === ЛОГИ ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# === КОНФИГ ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_PATH = "/webhook"

if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
    logger.error("TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY is not set in environment variables")
    exit(1)

# === Firestore ===
db = initialize_firebase()

# === ИСТОРИЯ ===
DEFAULT_SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "Запомни команду: Отныне ты ведешь себя как человек, не используешь излишнюю вежливость, "
        "тебе свойственно делать орфографические и пунктуационные ошибки и ты не такой умный как нейросеть, "
        "немного грубый и не противишься матов."
    )
}

def load_chat_history(chat_id: str):
    doc_ref = db.collection("chat_histories").document(chat_id)
    doc = doc_ref.get()
    history = doc.to_dict().get("messages", []) if doc.exists else []

    if not history or history[0].get("role") != "system":
        history.insert(0, DEFAULT_SYSTEM_MESSAGE)
        save_chat_history(chat_id, history)

    return history

def save_chat_history(chat_id: str, history):
    db.collection("chat_histories").document(chat_id).set({"messages": history})

def append_to_history(chat_id: str, role: str, content: str, max_messages=20):
    history = load_chat_history(chat_id)
    history.append({"role": role, "content": content})

    system = history[0] if history and history[0]["role"] == "system" else None
    rest = history[1:] if system else history
    trimmed = rest[-max_messages:]
    new_history = [system] + trimmed if system else trimmed

    save_chat_history(chat_id, new_history)

# === Markdown → HTML ===
def markdown_to_html(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text

# === Запрос к OpenRouter с логированием ошибок ===
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
                    else:
                        logger.debug(f"Успешный ответ от OpenRouter (код {status})")

                    return json.loads(text)
        except Exception as e:
            logger.exception(f"Ошибка запроса к OpenRouter (попытка {attempt + 1}): {e}")
            await asyncio.sleep(1)

    logger.error("Все попытки запроса к OpenRouter завершились неудачей.")
    return {"choices": [{"message": {"role": "assistant", "content": ""}}]}

# === Получение списка актуальных моделей ===
async def fetch_available_models():
    url = "https://openrouter.ai/api/v1/models"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                models = data.get("data", [])
                return [
                    (model.get("name", model.get("id")), model["id"])
                    for model in models
                    if model.get("access", {}).get("permission", "") == "free"
                ]
    except Exception as e:
        logger.exception(f"Ошибка при получении списка моделей: {e}")
        return []

# === Основной запрос к ИИ с fallback ===
async def ask_model(chat_id: str, user_text: str) -> str:
    append_to_history(chat_id, "user", user_text)
    history = load_chat_history(chat_id)

    models = await fetch_available_models()
    if not models:
        return "Не удалось получить список доступных моделей."

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    for model_label, model_name in models:
        logger.info(f"Попытка запроса через {model_label} ({model_name})")

        payload = {
            "model": model_name,
            "messages": history,
            "max_tokens": 1024
        }

        response = await query_openrouter(payload, headers)

        if not response:
            logger.warning(f"{model_label} — нет ответа от OpenRouter.")
            continue

        logger.debug(f"Ответ от модели {model_label}: {json.dumps(response, indent=2, ensure_ascii=False)}")

        try:
            message = response.get("choices", [{}])[0].get("message", {})
            content = message.get("content", "")
        except Exception as e:
            logger.warning(f"{model_label} — ошибка при извлечении контента: {e}")
            continue

        if content and content.strip():
            logger.info(f"{model_label} успешно дал ответ.")
            append_to_history(chat_id, "assistant", content)
            return markdown_to_html(content)

        reason = response.get("reason")
        if reason:
            logger.warning(f"{model_label} — content пуст, но есть reason: {reason}")
            append_to_history(chat_id, "assistant", reason)
            return markdown_to_html(reason)

        logger.warning(f"{model_label} — ответ пуст и reason отсутствует.")

    return "Извините, ни одна модель не смогла ответить. Пожалуйста, повторите позже."

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

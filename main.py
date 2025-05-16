import logging
import os
import requests
import datetime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from aiohttp import web

# Логгирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")  # https://yourapp.onrender.com

MODELS = {
    "deepseek": "deepseek/deepseek-r1:free",
    "gpt4o-mini": "gpt-4o-mini",
    "gpt4o": "gpt-4o",
}
DEFAULT_MODEL = "deepseek"
user_contexts = {}

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("DeepSeek R1", callback_data="model_deepseek")],
        [InlineKeyboardButton("GPT-4o-mini", callback_data="model_gpt4o-mini")],
        [InlineKeyboardButton("GPT-4o", callback_data="model_gpt4o")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    user_contexts[update.effective_user.id] = {
        "model": DEFAULT_MODEL,
        "history": []
    }
    await update.message.reply_text("Привет! Я бот на базе OpenRouter. Выбери модель ИИ:", reply_markup=reply_markup)

# Обработка кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data.startswith("model_"):
        chosen = query.data[len("model_"):]
        if chosen in MODELS:
            user_contexts.setdefault(user_id, {"model": chosen, "history": []})
            user_contexts[user_id]["model"] = chosen
            await query.edit_message_text(f"Вы выбрали модель: {chosen}")
        else:
            await query.edit_message_text("Неизвестная модель.")
    else:
        await query.edit_message_text("Неизвестная команда.")

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    if not user_text:
        logger.warning("Пустое сообщение")
        return

    if update.message.chat.type != "private":
        if not (update.message.entities or update.message.reply_to_message):
            return
        if "дипсик" not in user_text.lower() and not (
            update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
        ):
            return

    user_contexts.setdefault(user_id, {"model": DEFAULT_MODEL, "history": []})

    model_name = user_contexts[user_id]["model"]
    model_id = MODELS.get(model_name, DEFAULT_MODEL)

    user_contexts[user_id]["history"].append({"role": "user", "content": user_text})
    if len(user_contexts[user_id]["history"]) > 10:
        user_contexts[user_id]["history"].pop(0)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model_id,
        "messages": user_contexts[user_id]["history"]
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        result = response.json()

        if "choices" not in result:
            error = result.get("error", {})
            if error.get("code") == 429:
                reset_ms = error.get("metadata", {}).get("headers", {}).get("X-RateLimit-Reset")
                if reset_ms:
                    utc_reset = datetime.datetime.utcfromtimestamp(int(reset_ms) / 1000)
                    msk_tz = pytz.timezone("Europe/Moscow")
                    msk_reset = utc_reset.replace(tzinfo=pytz.utc).astimezone(msk_tz)
                    reset_str = msk_reset.strftime('%Y-%m-%d %H:%M:%S')
                    reply_text = (
                        "🚫 Превышен лимит бесплатных запросов к OpenRouter.\n"
                        f"⏳ Лимит обновится по МСК: {reset_str}."
                    )
                else:
                    reply_text = "🚫 Превышен лимит бесплатных запросов к OpenRouter. Попробуйте позже."
            else:
                reply_text = "⚠️ Не удалось получить ответ от модели. Попробуйте позже."
        else:
            reply_text = result["choices"][0]["message"]["content"].strip() or "Ответ пуст. Повторите вопрос."
            user_contexts[user_id]["history"].append({"role": "assistant", "content": reply_text})

    except Exception as e:
        logger.error(f"Ошибка при запросе к OpenRouter: {e} | Ответ: {response.text if 'response' in locals() else 'нет ответа'}")
        reply_text = "Произошла ошибка при обработке запроса."

    await update.message.reply_text(reply_text)

# Обработка Telegram webhook
async def handle_webhook(request):
    data = await request.json()
    logger.info(f"Получен апдейт на /webhook: {data}")
    app = request.app['telegram_app']
    update = Update.de_json(data, app.bot)
    await app.update_queue.put(update)
    return web.Response(text="OK")


# Установка и удаление webhook
async def on_startup(app):
    logger.info("🔄 Старт приложения. Устанавливаем webhook...")
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    await app['telegram_app'].bot.delete_webhook()
    await app['telegram_app'].bot.set_webhook(webhook_url)
    logger.info(f"✅ Webhook установлен: {webhook_url}")

async def on_cleanup(app):
    logger.info("🧹 Завершается приложение. Удаляем webhook...")
    await app['telegram_app'].bot.delete_webhook()

# Главная функция
def main():
    telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app = web.Application()
    app['telegram_app'] = telegram_app
    app.router.add_post('/webhook', handle_webhook)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    import asyncio

    async def runner():
        await telegram_app.initialize()
        await telegram_app.start()  # это включает обработку update_queue
    
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000)))
        await site.start()
        logger.info("====== Webhook сервер запущен ======")
    
        while True:
            await asyncio.sleep(3600)


    asyncio.run(runner())

if __name__ == "__main__":
    main()

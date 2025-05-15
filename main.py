import logging
import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

# Поддерживаемые модели (можно расширять)
MODELS = {
    "deepseek": "deepseek/deepseek-r1:free",
    "gpt4o-mini": "gpt-4o-mini",
    "gpt4o": "gpt-4o",
}

# Хранение контекста для каждого пользователя (user_id)
user_contexts = {}

# Выбор модели по умолчанию
DEFAULT_MODEL = "deepseek"

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
    await update.message.reply_text(
        "Привет! Я бот на базе OpenRouter. Выбери модель ИИ:", reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Обработка выбора модели
    if query.data.startswith("model_"):
        chosen = query.data[len("model_"):]
        if chosen in MODELS:
            if user_id not in user_contexts:
                user_contexts[user_id] = {"model": chosen, "history": []}
            else:
                user_contexts[user_id]["model"] = chosen
                # **Не очищаем историю при смене модели, сохраняем контекст**
            await query.edit_message_text(text=f"Вы выбрали модель: {chosen}")
        else:
            await query.edit_message_text(text="Неизвестная модель.")
    else:
        await query.edit_message_text(text="Неизвестная команда.")

MAX_MESSAGE_LENGTH = 1500  # можно менять по желанию

async def check_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OPENROUTER_API_KEY:
        await update.message.reply_text("API ключ OpenRouter не установлен.")
        return

    url = "https://openrouter.ai/api/v1/auth/key"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}"
    }

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            await update.message.reply_text(
                f"Ошибка запроса к OpenRouter API. HTTP {response.status_code}.\n"
                f"Ответ сервера: {response.text[:1500]}"
            )
            return

        data = response.json()

        remaining = data.get("limit_remaining")
        rate_limit = data.get("rate_limit", {})
        requests_limit = rate_limit.get("requests")
        interval = rate_limit.get("interval")

        if remaining is None and requests_limit is None:
            await update.message.reply_text("Информация о лимите недоступна.")
            return

        if remaining is None:
            remaining = requests_limit

        msg = f"На сегодняшний день осталось {remaining} запросов."
        if interval:
            msg += f" Лимит обновится каждые {interval}."

        await update.message.reply_text(msg)

    except Exception as e:
        logger.error(f"Ошибка при запросе лимита: {e}")
        await update.message.reply_text(f"Ошибка при запросе лимита: {e}")



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    # Проверяем, стоит ли отвечать на это сообщение
    if update.message.chat.type != "private":
        # В группе — отвечаем только если упомянут бот или ответ на его сообщение
        if not (update.message.entities or update.message.reply_to_message):
            return
        # Можно улучшить проверку упоминания бота (сейчас просто проверим в тексте "дипсик")
        if "дипсик" not in user_text.lower() and not (
            update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
        ):
            return

    # Если пользователь новый, инициализируем данные
    if user_id not in user_contexts:
        user_contexts[user_id] = {"model": DEFAULT_MODEL, "history": []}

    model_name = user_contexts[user_id]["model"]
    model_id = MODELS.get(model_name, DEFAULT_MODEL)

    # Добавляем в историю контекста
    user_contexts[user_id]["history"].append({"role": "user", "content": user_text})

    # Ограничим длину истории, чтобы не перегружать запрос (например, последние 10 сообщений)
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

        reply_text = result['choices'][0]['message']['content']

        # Добавляем ответ модели в историю
        user_contexts[user_id]["history"].append({"role": "assistant", "content": reply_text})

    except Exception as e:
        logger.error(f"Ошибка при запросе к OpenRouter: {e} | Ответ: {response.text if 'response' in locals() else 'нет ответа'}")
        reply_text = "Извините, произошла ошибка при обработке вашего запроса."

    await update.message.reply_text(reply_text)


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("limit", check_limit))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запускается...")
    app.run_polling()

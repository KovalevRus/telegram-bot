import os
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Список доступных моделей
AVAILABLE_MODELS = [
    "openchat/openchat-3.5-0106:free",
    "meta-llama/llama-3-8b-instruct:free",
    "mistralai/mixtral-8x7b-instruct:free",
    "google/gemma-7b-it:free",
    "huggingfaceh4/zephyr-7b-beta:free"
]

# Контекст для каждого пользователя
user_context = {}

# Текущая модель для каждого пользователя
user_model = {}

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши сообщение или выбери модель с помощью /model")

# Команда /model — выбор модели
async def choose_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton(model.split("/")[-1], callback_data=f"model:{model}")]
        for model in AVAILABLE_MODELS
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Выберите модель:", reply_markup=reply_markup)

# Обработка выбора модели
async def handle_model_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    model = query.data.split("model:")[1]
    user_id = query.from_user.id
    user_model[user_id] = model
    await query.edit_message_text(f"Модель установлена: {model.split('/')[-1]}")

# Проверка: нужно ли боту отвечать
def should_respond(update: Update, bot_username: str) -> bool:
    msg = update.message
    if msg is None:
        return False
    if msg.chat.type == "private":
        return True
    if msg.reply_to_message and msg.reply_to_message.from_user.username == bot_username:
        return True
    if f"@{bot_username}" in msg.text:
        return True
    return False

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username
    if not should_respond(update, bot_username):
        return

    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    # Устанавливаем модель, если не выбрана
    model = user_model.get(user_id, "deepseek/deepseek-chat:free")

    # Получаем историю диалога
    history = user_context.get(user_id, [])
    history.append({"role": "user", "content": user_text})

    # Запрос к OpenRouter
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": history
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        result = response.json()
        if "choices" not in result:
            raise Exception(result.get("error", {}).get("message", "Неизвестная ошибка"))
        reply = result["choices"][0]["message"]["content"]
        history.append({"role": "assistant", "content": reply})
        user_context[user_id] = history[-10:]  # Обрезаем до последних 10 сообщений
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("Извините, произошла ошибка при обработке вашего запроса.")

# Запуск
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("model", choose_model))
    app.add_handler(CallbackQueryHandler(handle_model_selection))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен!")
    app.run_polling()


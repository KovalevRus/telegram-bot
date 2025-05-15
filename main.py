import logging
import requests
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
)

# Токены из переменных окружения
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

logging.basicConfig(level=logging.INFO)

# Словарь для хранения истории сообщений по chat_id + user_id
# Формат: {(chat_id, user_id): [{"role": "user", "content": "..."} , ...]}
dialog_contexts = {}

# Поддерживаемые модели
MODELS = {
    "deepseek": "deepseek/deepseek-r1:free",
    "gpt4all": "gpt4all/gpt4all-lora-quantized",
    "chatgpt": "openai/gpt-4o-mini",
}

# Хранение выбора модели для каждого пользователя (chat_id, user_id)
user_models = {}

# Максимальное количество сообщений в контексте для экономии лимита
MAX_CONTEXT_MESSAGES = 6


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот на базе DeepSeek R1.\n"
        "Команда /model — выбрать модель.\n"
        "Команда /лимит — проверить баланс.\n"
        "Чтобы поговорить, упоминайте меня словом 'дипсик' или отвечайте на моё сообщение."
    )


async def set_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("DeepSeek R1", callback_data="model_deepseek")],
        [InlineKeyboardButton("GPT4All", callback_data="model_gpt4all")],
        [InlineKeyboardButton("ChatGPT 4o-mini", callback_data="model_chatgpt")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите модель:", reply_markup=reply_markup)


async def model_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if data.startswith("model_"):
        model_key = data.split("_", 1)[1]
        if model_key in MODELS:
            user_models[(chat_id, user_id)] = MODELS[model_key]
            await query.edit_message_text(f"Модель успешно установлена: {model_key}")
        else:
            await query.edit_message_text("Неизвестная модель.")
    else:
        await query.edit_message_text("Неизвестная команда.")


async def check_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    try:
        response = requests.get("https://openrouter.ai/api/v1/dashboard/billing", headers=headers)
        data = response.json()
        credits = data.get("credits_remaining")
        if credits is not None:
            await update.message.reply_text(f"Текущий баланс кредитов: {credits}")
        else:
            await update.message.reply_text("Не удалось получить информацию о балансе.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при запросе лимита: {e}")


def is_bot_mentioned(update: Update) -> bool:
    message = update.message
    # Проверяем, есть ли упоминание "дипсик" в тексте (регистр неважен)
    if message.entities:
        for ent in message.entities:
            if ent.type == "mention":
                # Получаем упоминание из текста
                mention_text = message.text[ent.offset : ent.offset + ent.length].lower()
                if "дипсик" in mention_text:
                    return True
    # Альтернатива — ищем слово в тексте просто
    if message.text and "дипсик" in message.text.lower():
        return True
    # Проверяем, ответ ли это на сообщение бота
    if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
        return True
    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message is None or message.text is None:
        return  # Игнорируем другие типы сообщений

    # Отвечаем только если есть упоминание или ответ на бота
    if not (("дипсик" in message.text.lower()) or
            (message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id)):
        return

    chat_id = message.chat_id
    user_id = message.from_user.id
    user_key = (chat_id, user_id)

    # Получаем выбранную пользователем модель, или дефолтную
    model = user_models.get(user_key, MODELS["deepseek"])

    # Инициализируем контекст, если нет
    if user_key not in dialog_contexts:
        dialog_contexts[user_key] = []

    # Добавляем новое сообщение пользователя в контекст
    dialog_contexts[user_key].append({"role": "user", "content": message.text})

    # Ограничиваем длину контекста
    if len(dialog_contexts[user_key]) > MAX_CONTEXT_MESSAGES:
        dialog_contexts[user_key] = dialog_contexts[user_key][-MAX_CONTEXT_MESSAGES:]

    # Формируем запрос
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": dialog_contexts[user_key]
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        result = response.json()

        if "choices" in result and len(result["choices"]) > 0:
            reply = result["choices"][0]["message"]["content"]
            # Добавляем ответ бота в контекст
            dialog_contexts[user_key].append({"role": "assistant", "content": reply})
            await message.reply_text(reply)
        else:
            logging.error(f"Нет 'choices' в ответе API: {result}")
            await message.reply_text("Извините, произошла ошибка при обработке вашего запроса.")
    except Exception as e:
        logging.error(f"Ошибка при запросе к API: {e}")
        await message.reply_text("Извините, произошла ошибка при обработке вашего запроса.")


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("model", set_model))
    app.add_handler(CallbackQueryHandler(model_button))
    app.add_handler(CommandHandler("limit", check_limit))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен!")
    app.run_polling()

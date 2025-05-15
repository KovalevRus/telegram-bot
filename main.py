import logging
import requests
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from flask import Flask
from threading import Thread

# Flask-сервер для UptimeRobot
app_flask = Flask(__name__)
@app_flask.route('/')
def home():
    return "Бот работает!"

def run():
    app_flask.run(host='0.0.0.0', port=10000)

def keep_alive():
    Thread(target=run).start()

# Контекстный чат
user_histories = {}

# Модель по умолчанию
DEFAULT_MODEL = "mistralai/mistral-7b-instruct:free"

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я Telegram-бот. Напиши мне или упомяни в группе.")

# Выбор модели пользователем
async def choose_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Mistral", callback_data="mistralai/mistral-7b-instruct:free")],
        [InlineKeyboardButton("Gemma", callback_data="google/gemma-7b-it:free")],
        [InlineKeyboardButton("DeepSeek", callback_data="deepseek/deepseek-chat:free")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери модель:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    model = query.data
    user_id = query.from_user.id
    context.user_data["model"] = model
    await query.edit_message_text(f"✅ Модель установлена: {model.split('/')[-1].split(':')[0]}")

# Ответ на сообщения
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    is_group = update.effective_chat.type in ["group", "supergroup"]
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.username == context.bot.username
    mentioned = context.bot.username.lower() in message.text.lower()

    if is_group and not (is_reply_to_bot or mentioned):
        return

    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    model = context.user_data.get("model", DEFAULT_MODEL)
    history = user_histories.get(user_id, [])
    history.append({"role": "user", "content": user_text})
    if len(history) > 10:
        history = history[-10:]

    headers = {
        "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json"
    }

    data = {
        "model": model,
        "messages": history
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        json_response = response.json()
        reply = json_response["choices"][0]["message"]["content"]
        history.append({"role": "assistant", "content": reply})
        user_histories[user_id] = history
    except Exception as e:
        reply = "Извините, произошла ошибка при обработке вашего запроса."

    await update.message.reply_text(reply)

# Запуск
if __name__ == '__main__':
    keep_alive()
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(os.environ.get("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("model", choose_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.ALL, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & filters.UpdateType.MESSAGE, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & filters.ALL, handle_message))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.INLINE_QUERY, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHOSEN_INLINE_RESULT, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CALLBACK_QUERY, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.SHIPPING_QUERY, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.PRE_CHECKOUT_QUERY, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.POLL, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.POLL_ANSWER, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.MY_CHAT_MEMBER, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHAT_MEMBER, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHAT_JOIN_REQUEST, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CALLBACK_QUERY, button_handler))
    print("Бот запускается...")
    app.run_polling()

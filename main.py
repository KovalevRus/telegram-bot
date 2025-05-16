import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
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


def escape_html(text: str) -> str:
    """Экранирование HTML-символов для безопасности."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))


def markdown_to_html(md: str) -> str:
    text = escape_html(md)

    # Заголовки h1-h6 (#, ##, ### и т.д.) — стилизуем жирным и размером
    def header_replacer(m):
        level = len(m.group(1))
        size_map = {
            1: "22px",
            2: "20px",
            3: "18px",
            4: "16px",
            5: "14px",
            6: "13px",
        }
        size = size_map.get(level, "13px")
        content = m.group(2).strip()
        return f'<b><span style="font-size:{size};">{content}</span></b>'

    text = re.sub(r"^(#{1,6})\s*(.+)$", header_replacer, text, flags=re.MULTILINE)

    # Цитаты (строки, начинающиеся с "> ")
    def blockquote_replacer(m):
        content = m.group(1).strip()
        return f'<blockquote style="margin-left:10px; color:#555; font-style:italic;">{content}</blockquote>'

    text = re.sub(r"^>\s*(.+)$", blockquote_replacer, text, flags=re.MULTILINE)

    # Жирный **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Подчёркнутый __text__
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
    # Зачёркнутый ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # Курсив *text*
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    # Инлайн-код `code`
    text = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", text)
    # Блоки кода ```code```
    def code_block_replacer(m):
        code = m.group(1)
        return f'<pre style="background:#eee; padding:5px; border-radius:5px; white-space:pre-wrap;"><code>{code}</code></pre>'
    text = re.sub(r"```([\s\S]+?)```", code_block_replacer, text)

    # Списки с поддержкой вложенности
    def parse_lists(text):
        lines = text.split("\n")
        result = []
        stack = []
        pattern = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")

        for line in lines:
            m = pattern.match(line)
            if not m:
                while stack:
                    t, _ = stack.pop()
                    result.append(f"</{t}>")
                result.append(line)
                continue

            indent = len(m.group(1).replace('\t', '    '))
            bullet = m.group(2)
            content = m.group(3)
            list_type = "ol" if bullet.endswith(".") else "ul"

            while stack and indent < stack[-1][1]:
                t, _ = stack.pop()
                result.append(f"</{t}>")

            if not stack or indent > stack[-1][1]:
                result.append(f"<{list_type}>")
                stack.append((list_type, indent))
            elif stack[-1][0] != list_type:
                t, _ = stack.pop()
                result.append(f"</{t}>")
                result.append(f"<{list_type}>")
                stack.append((list_type, indent))

            result.append(f"<li>{content}</li>")

        while stack:
            t, _ = stack.pop()
            result.append(f"</{t}>")

        return "\n".join(result)

    text = parse_lists(text)

    # Изображения ![alt](url) — заменяем на ссылку с alt-текстом, так как Telegram не поддерживает img в HTML
    text = re.sub(r"!\[([^\]]*)]\(([^)]+)\)", r'<a href="\2">[image: \1]</a>', text)
    # Ссылки [text](url)
    text = re.sub(r"\[([^\]]+)]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Таблицы (простая поддержка)
    def table_replacer(m):
        raw_table = m.group(0).strip().split("\n")
        header = raw_table[0].strip().strip('|').split('|')
        align = raw_table[1].strip().strip('|').split('|')
        rows = raw_table[2:]

        html = ['<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">']
        html.append("<tr>")
        for h in header:
            html.append(f"<th>{h.strip()}</th>")
        html.append("</tr>")

        for row in rows:
            html.append("<tr>")
            cols = row.strip().strip('|').split('|')
            for col in cols:
                html.append(f"<td>{col.strip()}</td>")
            html.append("</tr>")

        html.append("</table>")
        return "\n".join(html)

    text = re.sub(
        r"((?:^\|.*\|$\n)(?:^\|[-: ]+\|$\n)(?:^\|.*\|$\n?)+)",
        table_replacer,
        text,
        flags=re.MULTILINE,
    )

    # Переносы строк → <br>
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("\n\n", "<br><br>").replace("\n", "<br>")

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

    if not message or not message.text:
        return
    # Обрабатываем только, если сообщение — ответ боту или упоминание бота
    if not (message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id) and not message.entities:
        return

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

# bot.py
import os
import logging
from datetime import datetime, time as dtime
from dotenv import load_dotenv
import nest_asyncio
nest_asyncio.apply()
from pytz import timezone as pytz_timezone

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder, MessageHandler, ContextTypes, filters,
    ConversationHandler, CallbackQueryHandler, CommandHandler
)

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

# -------------------- LOAD .env (local dev) --------------------
load_dotenv()  # for local development only; on Render we will set env vars directly

# -------------------- CONFIG --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOSS_ID = int(os.getenv("BOSS_ID")) if os.getenv("BOSS_ID") else None
SHEET_ID = os.getenv("SHEET_ID")
# Full content of service_account.json (as JSON string) stored in SERVICE_ACCOUNT_JSON env var
SERVICE_ACCOUNT_JSON = os.getenv("SERVICE_ACCOUNT_JSON")
# Base URL for webhook (e.g. https://myservice.onrender.com). Set on Render as WEBHOOK_BASE_URL
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- Google Sheets setup --------------------
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
gc = None
sheet_doc = None

SERVICE_ACCOUNT_PATH = "service_account.json"

def write_service_account_file_from_env():
    """If SERVICE_ACCOUNT_JSON env var is set, write it to file so google Credentials can read it."""
    if SERVICE_ACCOUNT_JSON:
        try:
            with open(SERVICE_ACCOUNT_PATH, "w", encoding="utf-8") as f:
                f.write(SERVICE_ACCOUNT_JSON)
            logger.info("Service account JSON written to file.")
            return True
        except Exception:
            logger.exception("Не вдалося записати service_account.json з ENV.")
            return False
    return False

def init_gsheets():
    global gc, sheet_doc
    if gc is not None:
        return
    try:
        # Ensure service account file exists (either committed locally for dev, or written from ENV)
        if not os.path.exists(SERVICE_ACCOUNT_PATH):
            ok = write_service_account_file_from_env()
            if not ok:
                logger.warning("SERVICE_ACCOUNT_JSON не знайдено і service_account.json відсутній.")
                gc = None
                sheet_doc = None
                return

        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sheet_doc = gc.open_by_key(SHEET_ID) if SHEET_ID else None
        logger.info("Google Sheets: підключення встановлено.")
    except Exception:
        logger.exception("Не вдалося підключитися до Google Sheets:")
        gc = None
        sheet_doc = None

def append_user_hours(display_name, username, dt_str, start, end, lunch_minutes, total_hours):
    try:
        init_gsheets()
        if sheet_doc is None:
            logger.warning("Google Sheets не ініціалізовано — пропускаємо запис.")
            return False

        sheet_title = f"{display_name}_{username}"[:100] if username else display_name[:100]

        try:
            user_sheet = sheet_doc.worksheet(sheet_title)
        except gspread.exceptions.WorksheetNotFound:
            user_sheet = sheet_doc.add_worksheet(title=sheet_title, rows="2000", cols="7")
            user_sheet.append_row(["Дата", "Початок", "Кінець", "Обід (хв)", "Відпрацьовано"])
            logger.info(f"Створено новий лист '{sheet_title}' з заголовком.")

        month_map = {
            "January": "Січень", "February": "Лютий", "March": "Березень",
            "April": "Квітень", "May": "Травень", "June": "Червень",
            "July": "Липень", "August": "Серпень", "September": "Вересень",
            "October": "Жовтень", "November": "Листопад", "December": "Грудень"
        }
        current_month = f"{month_map[datetime.now().strftime('%B')]} {datetime.now().strftime('%Y')}"

        values = user_sheet.get_all_values()
        month_rows = [row for row in values if row and row[0] == current_month]

        if not month_rows:
            user_sheet.append_row([""])
            user_sheet.append_row([current_month])
            logger.info(f"Додано блок місяця '{current_month}' у лист '{sheet_title}'.")

        user_sheet.append_row([
            dt_str,
            start,
            end,
            int(lunch_minutes),
            float(total_hours)
        ])
        logger.info(f"Запис додано в листок '{sheet_title}': {dt_str} {start}-{end} {total_hours}h")
        return True

    except Exception:
        logger.exception("Помилка при записі в таблицю:")
        return False

# -------------------- Bot logic (як у тебе) --------------------
START_TIME, END_TIME, LUNCH = range(3)
user_data = {}
last_report_date = {}
known_users = set()

def fix_time_format(raw_time: str) -> str | None:
    raw = raw_time.strip().replace(".", ":").replace("-", ":")
    if raw.isdigit():
        if len(raw) == 1:
            raw = f"0{raw}:00"
        elif len(raw) == 2:
            raw = f"{raw}:00"
        elif len(raw) == 3:
            raw = f"0{raw[0]}:{raw[1:]}"
        elif len(raw) == 4:
            raw = f"{raw[:2]}:{raw[2:]}"
    try:
        from datetime import datetime
        dt = datetime.strptime(raw, "%H:%M")
        return dt.strftime("%H:%M")
    except ValueError:
        return None

def keyboard_main():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🟢 Почати звіт", callback_data="begin")]])

def keyboard_for_start_time():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🕘 Початок: зараз", callback_data="now_start")]])

def keyboard_for_end_time():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🕔 Кінець: зараз", callback_data="now_end")]])

# CallbackQuery
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "begin":
        await query.edit_message_text("Введи час початку роботи або натисни кнопку:")
        await query.message.reply_text("Вибери або введи час:", reply_markup=keyboard_for_start_time())
        return START_TIME

    if query.data == "now_start":
        now = datetime.now().strftime("%H:%M")
        user_data[user_id] = {"start": now}
        await query.edit_message_text(f"🕘 Початок встановлено: {now}")
        await query.message.reply_text("Введи час завершення або натисни:", reply_markup=keyboard_for_end_time())
        return END_TIME

    if query.data == "now_end":
        now = datetime.now().strftime("%H:%M")
        if user_id not in user_data or "start" not in user_data[user_id]:
            await query.edit_message_text("Спочатку вкажи час початку (натисни «Почати звіт»).")
            return START_TIME
        user_data[user_id]["end"] = now
        await query.edit_message_text(f"🕔 Кінець встановлено: {now}")
        await query.message.reply_text("Скільки хвилин обіду віднімати? Введи число (наприклад, 30).")
        return LUNCH

# Text handlers (start/end/lunch) — без змін
async def get_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    t = fix_time_format(update.message.text)
    if not t:
        await update.message.reply_text("❗️Невірний формат. Спробуй ще раз (09:00 або 1730).")
        return START_TIME
    user_data[user_id] = {"start": t}
    await update.message.reply_text("Добре ✅ Тепер введи час завершення:", reply_markup=keyboard_for_end_time())
    return END_TIME

async def get_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    t = fix_time_format(update.message.text)
    if not t:
        await update.message.reply_text("❗️Невірний формат. Спробуй ще раз (17:30 або 1730).")
        return END_TIME
    user_data[user_id]["end"] = t
    await update.message.reply_text("Скільки хвилин обіду віднімати? Введи число (наприклад, 30).")
    return LUNCH

async def get_lunch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        lunch_minutes = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❗️Введи лише число (наприклад, 30).")
        return LUNCH

    data = user_data.get(user_id, {})
    if "start" not in data or "end" not in data:
        await update.message.reply_text("Помилка: спробуй знову.")
        return ConversationHandler.END

    fmt = "%H:%M"
    start_dt = datetime.strptime(data["start"], fmt)
    end_dt = datetime.strptime(data["end"], fmt)
    total_hours = (end_dt - start_dt).seconds / 3600 - lunch_minutes / 60
    date_today = datetime.now().strftime("%d.%m.%Y")

    text = (
        f"📅 {date_today}\n"
        f"🕘 Початок: {data['start']}\n"
        f"🕔 Кінець: {data['end']}\n"
        f"🍽️ Обід: {lunch_minutes} хв\n"
        f"⏱️ Всього: {total_hours:.1f} год"
    )

    if BOSS_ID:
        await context.bot.send_message(chat_id=BOSS_ID, text=f"📨 Звіт від {update.effective_user.first_name}:\n\n{text}")
    await update.message.reply_text("✅ Дані надіслані босу!")

    append_user_hours(update.effective_user.first_name, update.effective_user.username,
                      date_today, data["start"], data["end"], lunch_minutes, total_hours)
    last_report_date[user_id] = date_today
    user_data.pop(user_id, None)
    return ConversationHandler.END

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    known_users.add(user.id)
    await update.message.reply_text("🟢 Натисни, щоб почати звіт:", reply_markup=keyboard_main())

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    known_users.add(user.id)
    await update.message.reply_text(f"Привіт, {user.first_name}! 👋\nГотовий заповнити звіт?", reply_markup=keyboard_main())

async def handle_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    init_gsheets()
    try:
        sheet_title = f"{user.first_name}_{user.username}"[:100] if user.username else user.first_name[:100]
        user_sheet = sheet_doc.worksheet(sheet_title)

        today = datetime.now().strftime("%d.%m.%Y")
        rows = user_sheet.get_all_values()

        records = []
        for row in rows:
            if not row or row[0].strip() == "":
                continue
            if "Дата" in row[0]:
                continue
            if any(month in row[0] for month in 
                  ["Січень","Лютий","Березень","Квітень","Травень",
                   "Червень","Липень","Серпень","Вересень","Жовтень",
                   "Листопад","Грудень"]):
                continue

            if len(row) >= 5:
                records.append({
                    "Дата": row[0],
                    "Початок": row[1],
                    "Кінець": row[2],
                    "Обід": row[3],
                    "Години": row[4]
                })

        today_records = [r for r in records if r["Дата"] == today]

        if not today_records:
            await update.message.reply_text("Сьогодні ще немає записів.")
        else:
            msg = "\n".join(
                [f"{r['Початок']} - {r['Кінець']} (Обід {r['Обід']} хв, {r['Години']} год)" 
                 for r in today_records]
            )
            await update.message.reply_text(f"📅 Звіт за сьогодні:\n{msg}")

    except Exception:
        logger.exception("handle_today error:")
        await update.message.reply_text("Помилка або записів немає.")

async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    init_gsheets()
    try:
        sheet_title = f"{user.first_name}_{user.username}"[:100] if user.username else user.first_name[:100]
        user_sheet = sheet_doc.worksheet(sheet_title)
        
        rows = user_sheet.get_all_values()

        records = []
        for row in rows:
            if not row or row[0].strip() == "":
                continue
            if "Дата" in row[0]:
                continue
            if any(month in row[0] for month in 
                  ["Січень","Лютий","Березень","Квітень","Травень",
                   "Червень","Липень","Серпень","Вересень","Жовтень",
                   "Листопад","Грудень"]):
                continue

            if len(row) >= 5:
                records.append({
                    "Дата": row[0],
                    "Початок": row[1],
                    "Кінець": row[2],
                    "Години": row[4]
                })

        if not records:
            await update.message.reply_text("Історія порожня.")
        else:
            last_10 = records[-10:]
            msg = "\n".join(
                [f"{r['Дата']}: {r['Початок']} - {r['Кінець']} ({r['Години']} год)"
                 for r in last_10]
            )
            await update.message.reply_text(f"📜 Останні записи:\n{msg}")

    except Exception:
        logger.exception("handle_history error:")
        await update.message.reply_text("Помилка або записів немає.")

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start – Почати звіт\n"
        "/today – Звіт за сьогодні\n"
        "/history – Історія звітів\n"
    )

async def send_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().strftime("%d.%m.%Y")
    sent = 0
    for user_id in known_users:
        if last_report_date.get(user_id) == today:
            continue
        try:
            await context.bot.send_message(chat_id=user_id,
                                           text="⏰ Не забудь заповнити звіт! Натисни «🟢 Почати звіт».",
                                           reply_markup=keyboard_main())
            sent += 1
        except Exception:
            logger.exception(f"Не вдалося надіслати нагадування -> {user_id}")
    logger.info(f"Send reminder job finished. Sent: {sent}")

# -------------------- Main (webhook) --------------------
async def main():
    # Create app
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(callback_handler, pattern="^begin$")],
        states={
            START_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_start_time),
                CallbackQueryHandler(callback_handler, pattern="^now_start$")
            ],
            END_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_end_time),
                CallbackQueryHandler(callback_handler, pattern="^now_end$")
            ],
            LUNCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_lunch)],
        },
        fallbacks=[],
        per_message=False,
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_any_message))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("today", handle_today))
    app.add_handler(CommandHandler("history", handle_history))
    app.add_handler(CommandHandler("help", handle_help))

    await app.bot.set_my_commands([
        BotCommand("start", "Почати роботу з ботом"),
        BotCommand("today", "Показати звіт за сьогодні"),
        BotCommand("history", "Переглянути попередні звіти"),
        BotCommand("help", "Коротка інструкція"),
    ])

    # Schedule reminders
    tz = pytz_timezone("Europe/Kyiv")
    app.job_queue.run_daily(send_reminder_job, time=dtime(hour=21, minute=0, tzinfo=tz))
    logger.info("✅ Бот конфігурований. Готуємо webhook...")

    # Ensure google creds file exists (for Sheets)
    write_service_account_file_from_env()

    # WEBHOOK: get port from env (Render provides $PORT)
    port = int(os.environ.get("PORT", "8000"))
    # URL path can be token (secure) or custom; краще використовувати токен як шлях
    url_path = BOT_TOKEN  # endpoint will be /<BOT_TOKEN>
    # WEBHOOK_BASE_URL must be set (e.g. https://myservice.onrender.com)
    if not WEBHOOK_BASE_URL:
        logger.warning("WEBHOOK_BASE_URL не виставлено — потрібно встановити в ENV (наприклад https://myservice.onrender.com).")
    else:
        webhook_url = f"{WEBHOOK_BASE_URL}/{url_path}"
        logger.info(f"Registering webhook: {webhook_url}")
        await app.bot.set_webhook(webhook_url)

    # Run as webhook server
    await app.run_webhook(listen="0.0.0.0", port=port, url_path=url_path)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

import gspread
from google.oauth2.service_account import Credentials
import os
import logging
from datetime import datetime, time as dtime
from dotenv import load_dotenv
import nest_asyncio
nest_asyncio.apply()
from pytz import timezone as pytz_timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder, MessageHandler, ContextTypes, filters,
    ConversationHandler, CallbackQueryHandler, CommandHandler
)

# -------------------- Налаштування --------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOSS_ID = int(os.getenv("BOSS_ID"))
SHEET_ID = os.getenv("SHEET_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets setup
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
gc = None
sheet_doc = None

def init_gsheets():
    global gc, sheet_doc
    if gc is not None:
        return
    try:
        creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
        gc = gspread.authorize(creds)
        sheet_doc = gc.open_by_key(SHEET_ID)
        logger.info("Google Sheets: підключення встановлено.")
    except Exception:
        logger.exception("Не вдалося підключитися до Google Sheets:")
        gc = None
        sheet_doc = None

def append_user_hours(display_name, username, dt_str, start, end, lunch_minutes, total_hours):
    """
    Додає запис у листок користувача без колонок Ім'я/Username.
    Якщо почався новий місяць — додає відступ + заголовок місяця (без повторного заголовка таблиці).
    Заголовок таблиці (Дата, Початок, ...) створюється лише при створенні листа.
    """
    try:
        init_gsheets()
        if sheet_doc is None:
            logger.warning("Google Sheets не ініціалізовано — пропускаємо запис.")
            return False

        # Назва аркуша
        sheet_title = f"{display_name}_{username}"[:100] if username else display_name[:100]

        try:
            user_sheet = sheet_doc.worksheet(sheet_title)
            sheet_exists = True
        except gspread.exceptions.WorksheetNotFound:
            # Створюємо листок і одразу додаємо заголовок у перший рядок
            user_sheet = sheet_doc.add_worksheet(title=sheet_title, rows="2000", cols="7")
            user_sheet.append_row(["Дата", "Початок", "Кінець", "Обід (хв)", "Відпрацьовано"])
            sheet_exists = False
            logger.info(f"Створено новий лист '{sheet_title}' з заголовком.")

        # --- Додаємо розділення по місяцях (але НЕ дублюємо заголовок таблиці) ---
        month_map = {
            "January": "Січень", "February": "Лютий", "March": "Березень",
            "April": "Квітень", "May": "Травень", "June": "Червень",
            "July": "Липень", "August": "Серпень", "September": "Вересень",
            "October": "Жовтень", "November": "Листопад", "December": "Грудень"
        }
        current_month = f"{month_map[datetime.now().strftime('%B')]} {datetime.now().strftime('%Y')}"

        # Отримаємо всі значення для перевірки наявності блоку місяця у першій колонці
        values = user_sheet.get_all_values()  # список рядків (списків)
        # Перевіримо чи вже вставлений рядок з назвою місяця (точне співпадіння у першій колонці)
        month_rows = [row for row in values if row and row[0] == current_month]

        if not month_rows:
            # Додаємо тільки: пустий рядок і назву місяця (НЕ додаємо заголовок таблиці зверху)
            user_sheet.append_row([""])  # відступ
            user_sheet.append_row([current_month])
            logger.info(f"Додано блок місяця '{current_month}' у лист '{sheet_title}'.")
            # Не додаємо повторний рядок заголовків — заголовок має залишатися першим рядком листа

        # Додаємо фактичний запис після блоку/в кінці листа
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

# -------------------- Константи / стани --------------------
START_TIME, END_TIME, LUNCH = range(3)
user_data = {}
last_report_date = {}
known_users = set()

# -------------------- Помічники --------------------
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

# -------------------- CallbackQuery --------------------
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

# -------------------- Текстові хендлери --------------------
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

    await context.bot.send_message(chat_id=BOSS_ID, text=f"📨 Звіт від {update.effective_user.first_name}:\n\n{text}")
    await update.message.reply_text("✅ Дані надіслані босу!")

    append_user_hours(update.effective_user.first_name, update.effective_user.username,
                      date_today, data["start"], data["end"], lunch_minutes, total_hours)
    last_report_date[user_id] = date_today
    user_data.pop(user_id, None)
    return ConversationHandler.END

# -------------------- Хендлер на будь-яке повідомлення --------------------
async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    known_users.add(user.id)
    await update.message.reply_text("🟢 Натисни, щоб почати звіт:", reply_markup=keyboard_main())

# -------------------- /start --------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    known_users.add(user.id)
    await update.message.reply_text(f"Привіт, {user.first_name}! 👋\nГотовий заповнити звіт?", reply_markup=keyboard_main())

# -------------------- Нові команди --------------------
async def handle_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    init_gsheets()
    try:
        sheet_title = f"{user.first_name}_{user.username}"[:100] if user.username else user.first_name[:100]
        user_sheet = sheet_doc.worksheet(sheet_title)
        
        today = datetime.now().strftime("%d.%m.%Y")
        rows = user_sheet.get_all_values()

        # Пропускаємо рядки з місяцями, пусті та заголовок
        records = []
        for row in rows:
            if not row or row[0].strip() == "":
                continue
            if "Дата" in row[0]:
                continue
            # Якщо це блок місяця — пропускаємо
            if any(month in row[0] for month in 
                  ["Січень","Лютий","Березень","Квітень","Травень",
                   "Червень","Липень","Серпень","Вересень","Жовтень",
                   "Листопад","Грудень"]):
                continue
            
            # Формуємо запис
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

        # Пропускаємо місяці, пусті рядки, заголовок
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

# -------------------- Нагадування --------------------
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

# -------------------- Основна функція --------------------
async def main():
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

    tz = pytz_timezone("Europe/Kyiv")
    app.job_queue.run_daily(send_reminder_job, time=dtime(hour=21, minute=0, tzinfo=tz))
    logger.info("✅ Бот запущено. Нагадування о 21:00 за Києвом.")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

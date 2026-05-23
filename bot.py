import logging
import sqlite3
import asyncio
import os
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --- Configuration ---
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("No BOT_TOKEN provided in environment variables!")

WEBAPP_URL = os.getenv(
    'WEBAPP_URL',
    'https://PASTE-YOUR-REAL-RAILWAY-LINK-HERE.up.railway.app/'
)

admin_ids_raw = os.getenv('ADMIN_IDS', '6197579049')
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(',') if x.strip().isdigit()]

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect('task_bot.db')
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0,
        referred_by INTEGER,
        upi_id TEXT,
        is_banned INTEGER DEFAULT 0,
        device_verified INTEGER DEFAULT 0,
        device_token TEXT
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_data TEXT,
        status TEXT DEFAULT 'available',
        assigned_to INTEGER,
        assigned_at TEXT,
        submission_data TEXT
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (
        chat_id TEXT PRIMARY KEY,
        invite_link TEXT
    )''')

    cursor.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('menu_text', 'Welcome to the Task Bot! Complete tasks to earn INR.')"
    )
    cursor.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('bot_status', 'ON')"
    )
    cursor.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('withdrawal_status', 'ON')"
    )
    cursor.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('total_wd_processed', '0')"
    )

    conn.commit()
    conn.close()

init_db()

def db_query(query, params=(), commit=False, fetchall=False, fetchone=False):
    conn = sqlite3.connect('task_bot.db')
    cursor = conn.cursor()
    cursor.execute(query, params)

    res = None

    if commit:
        conn.commit()

    if fetchall:
        res = cursor.fetchall()
    elif fetchone:
        res = cursor.fetchone()

    conn.close()
    return res

async def check_user_joined_channels(bot, user_id):
    channels = db_query(
        "SELECT chat_id FROM channels",
        fetchall=True
    )

    if not channels:
        return True

    for row in channels:
        try:
            c_id = row[0].strip()

            if c_id.startswith("-") or c_id.isdigit():
                c_id = int(c_id)

            member = await bot.get_chat_member(
                chat_id=c_id,
                user_id=user_id
            )

            if member.status in ['left', 'kicked']:
                return False

        except:
            return False

    return True

def get_channel_verification_keyboard():
    channels = db_query(
        "SELECT invite_link FROM channels",
        fetchall=True
    )

    keyboard = []
    row = []

    for i, row_data in enumerate(channels):
        row.append(
            InlineKeyboardButton(
                f"Join Channel {i+1}",
                url=row_data[0]
            )
        )

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(
            "Verify Channels",
            callback_data="check_membership"
        )
    ])

    return InlineKeyboardMarkup(keyboard)

def get_webapp_verify_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                "Verify Your Device",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)

def get_main_menu_keyboard(user_id):
    keyboard = [
        [KeyboardButton("📝 Get Task")],
        [KeyboardButton("💰 Wallet"), KeyboardButton("💸 Withdraw")],
        [KeyboardButton("👥 Refer & Earn"), KeyboardButton("📞 Support")]
    ]

    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton("⚙️ Admin Panel")])

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def task_timeout_monitor(context: ContextTypes.DEFAULT_TYPE):
    cutoff = (datetime.now() - timedelta(minutes=30)).isoformat()

    expired = db_query(
        "SELECT id, assigned_to FROM tasks WHERE status = 'assigned' AND assigned_at < ?",
        (cutoff,),
        fetchall=True
    )

    for tid, uid in expired:
        db_query(
            "UPDATE tasks SET status = 'available', assigned_to = NULL, assigned_at = NULL WHERE id = ?",
            (tid,),
            commit=True
        )

        try:
            await context.bot.send_message(
                chat_id=uid,
                text="⚠️ Task expired (30m limit). Released to queue."
            )
        except:
            pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, username = (
        update.effective_user.id,
        update.effective_user.username or "Unknown"
    )

    # 1. Maintenance Mode Check (Always first)
    bot_status = db_query(
        "SELECT value FROM config WHERE key='bot_status'",
        fetchone=True
    )[0]

    if bot_status == 'OFF' and user_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Maintenance mode.")
        return

    # 2. Database check for user state
    user = db_query(
        "SELECT is_banned, device_verified FROM users WHERE user_id = ?",
        (user_id,),
        fetchone=True
    )

    # 3. Check for ban
    if user and user[0] == 1:
        await update.message.reply_text("❌ Access Denied.")
        return

    # 4. If user not in DB, add them
    if not user:
        ref_id = (
            int(context.args[0])
            if context.args
            and context.args[0].isdigit()
            and int(context.args[0]) != user_id
            else None
        )

        db_query(
            "INSERT INTO users (user_id, username, referred_by, device_verified) VALUES (?, ?, ?, 0)",
            (user_id, username, ref_id),
            commit=True
        )

        device_verified = 0

    else:
        device_verified = user[1]

    # 5. Check if verified in DB
    if device_verified == 1:
        menu_text = db_query(
            "SELECT value FROM config WHERE key='menu_text'",
            fetchone=True
        )[0]

        await update.message.reply_text(
            menu_text,
            reply_markup=get_main_menu_keyboard(user_id)
        )
        return

    # 6. Force Channel Join
    if not await check_user_joined_channels(context.bot, user_id) and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⚠️ Join channels first:",
            reply_markup=get_channel_verification_keyboard()
        )
        return

    # 7. Force Device Verification
    await update.message.reply_text(
        "🔒 *Verify Yourself To Start Bot*\n\nPlease click the button below to complete a quick device security check.",
        parse_mode="Markdown",
        reply_markup=get_webapp_verify_keyboard()
    )

# KEEP ALL YOUR OTHER FUNCTIONS EXACTLY SAME BELOW THIS LINE
# Paste your remaining functions here without changing anything.

def main():
    app = Application.builder().token(TOKEN).build()

    app.job_queue.run_repeating(
        task_timeout_monitor,
        interval=60
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    app.run_polling()

if __name__ == '__main__':
    main()

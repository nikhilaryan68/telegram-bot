import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import os

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [6197579049]

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect("/tmp/task_bot.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0,
        referred_by INTEGER,
        upi_id TEXT,
        is_banned INTEGER DEFAULT 0
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
    defaults = [
        ('menu_text', 'Welcome to the Task Bot! Complete tasks to earn INR.'),
        ('bot_status', 'ON'),
        ('withdrawal_status', 'ON'),
        ('total_wd_processed', '0')
    ]
    cursor.executemany("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", defaults)
    conn.commit()
    conn.close()

init_db()

def db_query(query, params=(), commit=False, fetchall=False, fetchone=False):
    conn = sqlite3.connect('/tmp/task_bot.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = None
    if commit: conn.commit()
    if fetchall: res = cursor.fetchall()
    elif fetchone: res = cursor.fetchone()
    conn.close()
    return res

# --- Key Keyboards ---

def get_main_menu_keyboard(user_id):
    admin_contact_url = f"tg://user?id={ADMIN_IDS[0]}"
    keyboard = [
        [InlineKeyboardButton("📝 Get Task", callback_data="get_task")],
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet"), InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("👥 Refer & Earn", callback_data="refer_earn"), InlineKeyboardButton("📞 Support", url=admin_contact_url)]
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_channel_verification_keyboard():
    channels = db_query("SELECT invite_link FROM channels", fetchall=True)
    keyboard = [[InlineKeyboardButton(f"📢 Join Channel {i+1}", url=row[0])] for i, row in enumerate(channels)]
    keyboard.append([InlineKeyboardButton("🔄 Try Again / Verify", callback_data="check_membership")])
    return InlineKeyboardMarkup(keyboard)

# --- Monitoring ---

async def task_timeout_monitor(context: ContextTypes.DEFAULT_TYPE):
    cutoff = (datetime.now() - timedelta(minutes=30)).isoformat()
    expired = db_query("SELECT id, assigned_to FROM tasks WHERE status = 'assigned' AND assigned_at < ?", (cutoff,), fetchall=True)
    for tid, uid in expired:
        db_query("UPDATE tasks SET status = 'available', assigned_to = NULL, assigned_at = NULL WHERE id = ?", (tid,), commit=True)
        try: await context.bot.send_message(chat_id=uid, text="⚠️ Task expired (30m limit). Released to queue.")
        except: pass

# --- Core Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, username = update.effective_user.id, update.effective_user.username or "Unknown"
    res = db_query("SELECT value FROM config WHERE key='bot_status'", fetchone=True)
    bot_status = res[0] if res else 'ON'
    if bot_status == 'OFF' and user_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Maintenance mode.")
        return
    user = db_query("SELECT is_banned FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if user and user[0] == 1:
        await update.message.reply_text("❌ Banned.")
        return
    if not user:
        ref_id = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user_id else None
        db_query("INSERT INTO users (user_id, username, referred_by) VALUES (?, ?, ?)", (user_id, username, ref_id), commit=True)
    res_text = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)
    await update.message.reply_text(res_text[0], reply_markup=get_main_menu_keyboard(user_id))

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    # --- ADMIN PANEL LOGIC ---
    if data == "admin_panel" and user_id in ADMIN_IDS:
        kbd = [
            [InlineKeyboardButton("📤 Bulk Upload", callback_data="adm_bulk"), InlineKeyboardButton("📋 Tasks Queue", callback_data="adm_pending_tasks")],
            [InlineKeyboardButton("📥 Task Approvals", callback_data="adm_list_task_app"), InlineKeyboardButton("🏧 WD Requests", callback_data="adm_list_wd")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"), InlineKeyboardButton("💬 DM User", callback_data="adm_dm")],
            [InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"), InlineKeyboardButton("🔓 Unban User", callback_data="adm_unban")],
            [InlineKeyboardButton("Toggle WD", callback_data="adm_tog_wd"), InlineKeyboardButton("Toggle Bot", callback_data="adm_tog_bot")],
            [InlineKeyboardButton("🪙 Check Balance", callback_data="adm_chk_bal"), InlineKeyboardButton("💳 Mod Balance", callback_data="adm_mod_bal")],
            [InlineKeyboardButton("🏆 Top 10 Bal", callback_data="adm_top_bal"), InlineKeyboardButton("📝 Menu Text", callback_data="adm_chg_text")],
            [InlineKeyboardButton("📢 Manage Channels", callback_data="adm_manage_channels"), InlineKeyboardButton("🔍 Task Lookup", callback_data="adm_task_status_lookup")],
            [InlineKeyboardButton("📊 Bot Stats", callback_data="adm_stats")],
            [InlineKeyboardButton("❌ Close", callback_data="main_menu")]
        ]
        await query.message.edit_text("⚙️ **Admin Control Center**", reply_markup=InlineKeyboardMarkup(kbd))

    # Stats
    elif data == "adm_stats" and user_id in ADMIN_IDS:
        total_u = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
        total_t = db_query("SELECT COUNT(*) FROM tasks WHERE status='completed'", fetchone=True)[0]
        total_wd = db_query("SELECT value FROM config WHERE key='total_wd_processed'", fetchone=True)[0]
        await query.message.reply_text(f"📊 Stats:\nUsers: {total_u}\nCompleted Tasks: {total_t}\nPaid: ₹{total_wd}")

    # Toggles
    elif data == "adm_tog_bot" and user_id in ADMIN_IDS:
        curr = db_query("SELECT value FROM config WHERE key='bot_status'", fetchone=True)[0]
        new_v = 'OFF' if curr == 'ON' else 'ON'
        db_query("UPDATE config SET value=? WHERE key='bot_status'", (new_v,), commit=True)
        await query.message.reply_text(f"Bot Status set to: {new_v}")

    elif data == "adm_tog_wd" and user_id in ADMIN_IDS:
        curr = db_query("SELECT value FROM config WHERE key='withdrawal_status'", fetchone=True)[0]
        new_v = 'OFF' if curr == 'ON' else 'ON'
        db_query("UPDATE config SET value=? WHERE key='withdrawal_status'", (new_v,), commit=True)
        await query.message.reply_text(f"Withdrawals set to: {new_v}")

    # Task Management
    elif data == "adm_pending_tasks" and user_id in ADMIN_IDS:
        tasks = db_query("SELECT id FROM tasks WHERE status='available' LIMIT 20", fetchall=True)
        msg = "📋 Available IDs: " + ", ".join([str(t[0]) for t in tasks]) if tasks else "Queue Empty."
        await query.message.reply_text(msg)

    # Input Triggers (State Setters)
    elif data == "adm_bulk": context.user_data['state'] = 'ADM_WAITING_BULK'; await query.message.reply_text("Send: user:pass,user:pass")
    elif data == "adm_broadcast": context.user_data['state'] = 'ADM_BROADCAST'; await query.message.reply_text("Send message to broadcast:")
    elif data == "adm_dm": context.user_data['state'] = 'ADM_DM'; await query.message.reply_text("Send: user_id:message")
    elif data == "adm_ban": context.user_data['state'] = 'ADM_BAN'; await query.message.reply_text("Send User ID to ban:")
    elif data == "adm_unban": context.user_data['state'] = 'ADM_UNBAN'; await query.message.reply_text("Send User ID to unban:")
    elif data == "adm_chk_bal": context.user_data['state'] = 'ADM_CHK_BAL'; await query.message.reply_text("Send User ID:")
    elif data == "adm_mod_bal": context.user_data['state'] = 'ADM_MOD_BAL'; await query.message.reply_text("Send: user_id:amount (+ or -)")
    elif data == "adm_chg_text": context.user_data['state'] = 'ADM_CHG_TEXT'; await query.message.reply_text("Send new menu text:")
    elif data == "adm_task_status_lookup": context.user_data['state'] = 'ADM_LOOKUP_TASK'; await query.message.reply_text("Send Task ID:")

    # Top 10
    elif data == "adm_top_bal" and user_id in ADMIN_IDS:
        top = db_query("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10", fetchall=True)
        txt = "🏆 **Top 10 Balances**\n" + "\n".join([f"{i+1}. {r[0]}: ₹{r[1]}" for i, r in enumerate(top)])
        await query.message.reply_text(txt, parse_mode="Markdown")

    # --- USER ACTIONS ---
    elif data == "main_menu":
        res = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)
        await query.message.edit_text(res[0], reply_markup=get_main_menu_keyboard(user_id))

    elif data == "wallet":
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        await query.message.edit_text(f"💳 Balance: ₹{u[0]:.2f}\nUPI: `{u[1] or 'Not Set'}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Set UPI", callback_data="add_upi")], [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")], [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))

    elif data == "add_upi": 
        context.user_data['state'] = 'WAITING_UPI'
        await query.message.reply_text("Send your UPI ID:")

    elif data == "get_task":
        task = db_query("SELECT id, task_data FROM tasks WHERE status = 'available' LIMIT 1", fetchone=True)
        if not task:
            await query.message.reply_text("📭 No tasks available right now.")
            return
        tid, tdata = task
        db_query("UPDATE tasks SET status='assigned', assigned_to=?, assigned_at=? WHERE id=?", (user_id, datetime.now().isoformat(), tid), commit=True)
        await query.message.reply_text(f"✅ Task Assigned!\nData: `{tdata}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Submit", callback_data=f"subm_t_{tid}"), InlineKeyboardButton("❌ Cancel", callback_data=f"canc_t_{tid}")]]))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, text, state = update.effective_user.id, update.message.text.strip(), context.user_data.get('state')
    if not state: return
    context.user_data['state'] = None

    # User States
    if state == 'WAITING_UPI':
        db_query("UPDATE users SET upi_id=? WHERE user_id=?", (text, user_id), commit=True)
        await update.message.reply_text(f"✅ UPI set to: {text}")

    # Admin States
    elif state == 'ADM_WAITING_BULK':
        for pair in text.split(","):
            if ":" in pair: db_query("INSERT INTO tasks (task_data) VALUES (?)", (pair.strip(),), commit=True)
        await update.message.reply_text("✅ Bulk tasks added.")

    elif state == 'ADM_BROADCAST':
        users = db_query("SELECT user_id FROM users", fetchall=True)
        count = 0
        for u in users:
            try: await context.bot.send_message(u[0], f"📢 **Announcement**\n\n{text}", parse_mode="Markdown"); count += 1
            except: pass
        await update.message.reply_text(f"✅ Sent to {count} users.")

    elif state == 'ADM_MOD_BAL' and ":" in text:
        target, amt = text.split(":", 1)
        db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (float(amt), int(target)), commit=True)
        await update.message.reply_text("✅ Balance updated.")

    elif state == 'ADM_BAN':
        db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (int(text),), commit=True)
        await update.message.reply_text("🚫 User Banned.")

    elif state == 'ADM_CHG_TEXT':
        db_query("UPDATE config SET value=? WHERE key='menu_text'", (text,), commit=True)
        await update.message.reply_text("✅ Menu text updated.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(task_timeout_monitor, interval=60)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == '__main__':
    main()

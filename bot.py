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

# --- Keyboards ---

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

# --- Core Logic ---

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

    # --- ADMIN PANEL ---
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
            [InlineKeyboardButton("📊 Bot Stats", callback_data="adm_stats"), InlineKeyboardButton("💾 DB Backup", callback_data="adm_backup")],
            [InlineKeyboardButton("❌ Close", callback_data="main_menu")]
        ]
        await query.message.edit_text("⚙️ **Admin Control Panel**", reply_markup=InlineKeyboardMarkup(kbd))

    # Backup Retrieval Change
    elif data == "adm_backup" and user_id in ADMIN_IDS:
        try:
            with open("/tmp/task_bot.db", "rb") as f:
                await context.bot.send_document(chat_id=user_id, document=f, filename="task_bot_backup.db")
        except Exception as e: await query.message.reply_text(f"Error: {e}")

    # Manage Channels Fix
    elif data == "adm_manage_channels" and user_id in ADMIN_IDS:
        kb = [[InlineKeyboardButton("➕ Add", callback_data="adm_add_chan"), InlineKeyboardButton("❌ Rem", callback_data="adm_rem_chan")], [InlineKeyboardButton("📋 List", callback_data="adm_list_chan")], [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        await query.message.edit_text("📢 **Manage Verification Channels**", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "adm_add_chan": context.user_data['state'] = 'ADM_ADD_CHAN_DATA'; await query.message.reply_text("Send channel ID and link like: `-100xxxx:https://t.me/xxx`")
    elif data == "adm_rem_chan": context.user_data['state'] = 'ADM_REM_CHAN_DATA'; await query.message.reply_text("Send the Channel ID to remove:")
    elif data == "adm_list_chan":
        chans = db_query("SELECT chat_id, invite_link FROM channels", fetchall=True)
        txt = "📋 **Active Channels:**\n" + "\n".join([f"`{c[0]}` -> [Link]({c[1]})" for c in chans]) if chans else "No channels."
        await query.message.reply_text(txt, parse_mode="Markdown")

    # Refer & Earn Fix
    elif data == "refer_earn":
        bot = await context.bot.get_me()
        count = db_query("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,), fetchone=True)[0]
        ref_link = f"https://t.me/{bot.username}?start={user_id}"
        await query.message.edit_text(f"👥 **Refer & Earn**\n\nTotal Referrals: `{count}`\n\nYour Link: `{ref_link}`\n\nEarn rewards for every person you invite!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))

    # Withdrawal Fix
    elif data == "withdraw":
        status = db_query("SELECT value FROM config WHERE key='withdrawal_status'", fetchone=True)[0]
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if status == 'OFF': await query.message.reply_text("⚠️ Withdrawals are currently disabled by Admin."); return
        if not u[1]: await query.message.reply_text("❌ You must link your UPI ID in the Wallet first!"); return
        if u[0] < 5.0: await query.message.reply_text("❌ Minimum withdrawal is ₹5.00"); return
        context.user_data['state'] = 'WAITING_WD_AMOUNT'
        await query.message.reply_text(f"💰 Balance: ₹{u[0]}\n\nEnter amount to withdraw:")

    # Other Toggles/Stats
    elif data == "adm_stats" and user_id in ADMIN_IDS:
        total_u = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
        total_wd = db_query("SELECT value FROM config WHERE key='total_wd_processed'", fetchone=True)[0]
        await query.message.reply_text(f"📊 Stats:\nUsers: {total_u}\nPaid Out: ₹{total_wd}")
    elif data == "adm_tog_bot":
        c = db_query("SELECT value FROM config WHERE key='bot_status'", fetchone=True)[0]
        nv = 'OFF' if c == 'ON' else 'ON'
        db_query("UPDATE config SET value=? WHERE key='bot_status'", (nv,), commit=True); await query.message.reply_text(f"Bot is now {nv}")

    # Standard Menus
    elif data == "main_menu":
        res = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)
        await query.message.edit_text(res[0], reply_markup=get_main_menu_keyboard(user_id))
    elif data == "wallet":
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        await query.message.edit_text(f"💳 Wallet Balance: ₹{u[0]:.2f}\nUPI: `{u[1] or 'None'}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Set UPI", callback_data="add_upi")], [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")], [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))
    elif data == "add_upi": context.user_data['state'] = 'WAITING_UPI'; await query.message.reply_text("Send your UPI ID:")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, text, state = update.effective_user.id, update.message.text.strip(), context.user_data.get('state')
    if not state: return
    context.user_data['state'] = None

    if state == 'WAITING_UPI':
        db_query("UPDATE users SET upi_id=? WHERE user_id=?", (text, user_id), commit=True); await update.message.reply_text("✅ UPI Updated.")
    elif state == 'WAITING_WD_AMOUNT':
        try: 
            amt = float(text)
            u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
            if 0 < amt <= u[0]:
                await update.message.reply_text("✅ Withdrawal Request Sent!")
                for a in ADMIN_IDS: await context.bot.send_message(a, f"🏧 **New WD Request**\nUser: `{user_id}`\nAmount: ₹{amt}\nUPI: `{u[1]}`")
            else: await update.message.reply_text("❌ Invalid amount.")
        except: await update.message.reply_text("❌ Please enter a number.")
    elif state == 'ADM_ADD_CHAN_DATA' and ":" in text:
        cid, link = text.split(":", 1)
        db_query("INSERT OR REPLACE INTO channels (chat_id, invite_link) VALUES (?,?)", (cid.strip(), link.strip()), commit=True); await update.message.reply_text("✅ Channel added.")
    elif state == 'ADM_REM_CHAN_DATA':
        db_query("DELETE FROM channels WHERE chat_id=?", (text,), commit=True); await update.message.reply_text("✅ Channel removed.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(task_timeout_monitor, interval=60)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == '__main__': main()

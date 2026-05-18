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
    # Using /tmp/ ensures Railway has write permissions for the database
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

    # Default Config Values
    defaults = [
        ('menu_text', 'Welcome to the Task Bot! Complete tasks to earn INR.'),
        ('bot_status', 'ON'),
        ('withdrawal_status', 'ON'),
        ('total_wd_processed', '0')
    ]
    cursor.executemany("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", defaults)

    conn.commit()
    conn.close()

# Initialize database
init_db()

def db_query(query, params=(), commit=False, fetchall=False, fetchone=False):
    # Must use the same path as init_db
    conn = sqlite3.connect('/tmp/task_bot.db')
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

# --- Bot Logic Functions ---

async def check_user_joined_channels(bot, user_id):
    channels = db_query("SELECT chat_id FROM channels", fetchall=True)
    if not channels: return True
    for row in channels:
        try:
            c_id = row[0].strip()
            if c_id.startswith("-") or c_id.isdigit(): c_id = int(c_id)
            member = await bot.get_chat_member(chat_id=c_id, user_id=user_id)
            if member.status in ['left', 'kicked']: return False
        except: return False
    return True

def get_channel_verification_keyboard():
    channels = db_query("SELECT invite_link FROM channels", fetchall=True)
    keyboard = [[InlineKeyboardButton(f"📢 Join Channel {i+1}", url=row[0])] for i, row in enumerate(channels)]
    keyboard.append([InlineKeyboardButton("🔄 Try Again / Verify", callback_data="check_membership")])
    return InlineKeyboardMarkup(keyboard)

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

async def task_timeout_monitor(context: ContextTypes.DEFAULT_TYPE):
    cutoff = (datetime.now() - timedelta(minutes=30)).isoformat()
    expired = db_query("SELECT id, assigned_to FROM tasks WHERE status = 'assigned' AND assigned_at < ?", (cutoff,), fetchall=True)
    for tid, uid in expired:
        db_query("UPDATE tasks SET status = 'available', assigned_to = NULL, assigned_at = NULL WHERE id = ?", (tid,), commit=True)
        try: await context.bot.send_message(chat_id=uid, text="⚠️ Task expired (30m limit). Released to queue.")
        except: pass

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
    
    if not await check_user_joined_channels(context.bot, user_id) and user_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Join channels first:", reply_markup=get_channel_verification_keyboard())
        return
    
    res_text = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)
    menu_text = res_text[0] if res_text else "Welcome!"
    await update.message.reply_text(menu_text, reply_markup=get_main_menu_keyboard(user_id))

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

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
        await query.message.edit_text("⚙️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(kbd))

    elif data == "adm_stats" and user_id in ADMIN_IDS:
        total_u = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
        total_t = db_query("SELECT COUNT(*) FROM tasks WHERE status='completed'", fetchone=True)[0]
        total_wd = db_query("SELECT value FROM config WHERE key='total_wd_processed'", fetchone=True)[0]
        verified_u = 0
        all_u = db_query("SELECT user_id FROM users", fetchall=True)
        for u in all_u:
            if await check_user_joined_channels(context.bot, u[0]): verified_u += 1
        stats_msg = f"Total users: {total_u}\nVerified: {verified_u}\nTotal WD: ₹{total_wd}\nTasks Done: {total_t}"
        await query.message.reply_text(stats_msg)

    elif data == "check_membership":
        if await check_user_joined_channels(context.bot, user_id):
            await query.message.reply_text("✅ Verified!", reply_markup=get_main_menu_keyboard(user_id))
        else: await query.message.reply_text("❌ Join all channels.", reply_markup=get_channel_verification_keyboard())

    elif data == "main_menu":
        res = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)
        menu_text = res[0] if res else "Welcome!"
        await query.message.edit_text(menu_text, reply_markup=get_main_menu_keyboard(user_id))

    elif data == "get_task":
        active = db_query("SELECT id FROM tasks WHERE assigned_to = ? AND status IN ('assigned', 'pending_approval')", (user_id,), fetchone=True)
        if active: await query.message.reply_text("⚠️ Finish active task first."); return
        task = db_query("SELECT id, task_data FROM tasks WHERE status = 'available' LIMIT 1", fetchone=True)
        if not task: await query.message.reply_text("📭 No tasks."); return

        tid, tdata = task
        try: t_user, t_pass = tdata.split(":")
        except: await query.message.reply_text("⚠️ Task Error."); return

        db_query("UPDATE tasks SET status = 'assigned', assigned_to = ?, assigned_at = ? WHERE id = ?", (user_id, datetime.now().isoformat(), tid), commit=True)
        msg = f"TASK ID: {tid}\nUSER: `{t_user}`\nPASS: `{t_pass}`\nTimeout: 30m"
        await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Submit", callback_data=f"subm_t_{tid}"), InlineKeyboardButton("❌ Cancel", callback_data=f"canc_t_{tid}")]]))

    elif data.startswith("canc_t_"):
        tid = int(data.split("_")[2])
        db_query("UPDATE tasks SET status='available', assigned_to=NULL, assigned_at=NULL WHERE id=? AND assigned_to=?", (tid, user_id), commit=True)
        await query.message.edit_text("❌ Task canceled.")

    elif data.startswith("subm_t_"):
        tid = int(data.split("_")[2])
        db_query("UPDATE tasks SET status = 'pending_approval' WHERE id = ?", (tid,), commit=True)
        await query.message.edit_text("⏳ Submitted.")
        t_info = db_query("SELECT assigned_to, task_data FROM tasks WHERE id=?", (tid,), fetchone=True)
        for admin in ADMIN_IDS:
            try: await context.bot.send_message(admin, f"New Task Sub: {tid} by {t_info[0]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"adm_app_t_{tid}"), InlineKeyboardButton("Reject", callback_data=f"adm_rej_t_{tid}")]]))
            except: pass

    elif data == "wallet":
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        await query.message.edit_text(f"💳 Balance: ₹{u[0]:.2f}\nUPI: `{u[1] or 'None'}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Link UPI", callback_data="add_upi")], [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")], [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))

    elif data == "add_upi": context.user_data['state'] = 'WAITING_UPI'; await query.message.reply_text("Send UPI:")

    elif data == "withdraw":
        wd_s = db_query("SELECT value FROM config WHERE key='withdrawal_status'", fetchone=True)[0]
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if wd_s == 'OFF': await query.message.reply_text("⚠️ WD OFF"); return
        if not u[1]: await query.message.reply_text("❌ Link UPI first"); return
        context.user_data['state'] = 'WAITING_WD_AMOUNT'; await query.message.reply_text(f"Amt (Max ₹{u[0]}):")

    elif data == "refer_earn":
        bot_me = await context.bot.get_me()
        c = db_query("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,), fetchone=True)[0]
        await query.message.edit_text(f"👥 Referrals: {c}\nLink: `t.me/{bot_me.username}?start={user_id}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))

    # ... Admin functionality follows same pattern ...
    elif data == "adm_bulk": context.user_data['state'] = 'ADM_WAITING_BULK'; await query.message.reply_text("Format: u:p,u:p")
    elif data == "adm_broadcast": context.user_data['state'] = 'ADM_BROADCAST'; await query.message.reply_text("Msg:")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, text, state = update.effective_user.id, update.message.text.strip(), context.user_data.get('state')
    if not state: return
    context.user_data['state'] = None

    if state == 'WAITING_UPI': 
        db_query("UPDATE users SET upi_id=? WHERE user_id=?", (text, user_id), commit=True)
        await update.message.reply_text("UPI Linked.")

    elif state == 'WAITING_WD_AMOUNT':
        try: amt = float(text)
        except: return
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if 0 < amt <= u[0]:
            if 'withdrawals' not in context.bot_data: context.bot_data['withdrawals'] = {}
            wid = str(int(datetime.now().timestamp()))
            context.bot_data['withdrawals'][wid] = {'user_id': user_id, 'amount': amt, 'upi': u[1], 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            await update.message.reply_text("WD Requested.")
            for a in ADMIN_IDS:
                try: await context.bot.send_message(a, f"WD Req: {amt} by {user_id}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"adm_app_w_{wid}"), InlineKeyboardButton("Reject", callback_data=f"adm_rej_w_{wid}")]]))
                except: pass

    elif state == 'ADM_WAITING_BULK':
        for i in text.split(","):
            if ":" in i: db_query("INSERT INTO tasks (task_data) VALUES (?)", (i.strip(),), commit=True)
        await update.message.reply_text("Bulk added.")

    elif state == 'ADM_BROADCAST':
        for u in db_query("SELECT user_id FROM users", fetchall=True):
            try: await context.bot.send_message(u[0], f"📢 Announcement\n\n{text}")
            except: pass

def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(task_timeout_monitor, interval=60)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == '__main__':
    main()
    

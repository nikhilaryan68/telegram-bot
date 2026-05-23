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

WEBAPP_URL = os.getenv('WEBAPP_URL', 'https://mini-apo-production.up.railway.app/')

admin_ids_raw = os.getenv('ADMIN_IDS', '6197579049')
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(',') if x.strip().isdigit()]

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect('task_bot.db')
    cursor = conn.cursor()
    
    # Add device_token column if it doesn't exist
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN device_token TEXT")
    except sqlite3.OperationalError:
        pass (
        
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0,
        referred_by INTEGER,
        upi_id TEXT,
        is_banned INTEGER DEFAULT 0,
        device_verified INTEGER DEFAULT 0
    )''')
    
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN device_verified INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass 
    
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

    try:
        cursor.execute("SELECT chat_id FROM channels LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("DROP TABLE IF EXISTS channels")
        cursor.execute('''CREATE TABLE channels (chat_id TEXT PRIMARY KEY, invite_link TEXT)''')
    
    cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('menu_text', 'Welcome to the Task Bot! Complete tasks to earn INR.')")
    cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('bot_status', 'ON')")
    cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('withdrawal_status', 'ON')")
    cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('total_wd_processed', '0')")
    
    conn.commit()
    conn.close()

init_db()

def db_query(query, params=(), commit=False, fetchall=False, fetchone=False):
    conn = sqlite3.connect('task_bot.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = None
    if commit: conn.commit()
    if fetchall: res = cursor.fetchall()
    elif fetchone: res = cursor.fetchone()
    conn.close()
    return res

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
    keyboard = []
    row = []
    for i, row_data in enumerate(channels):
        row.append(InlineKeyboardButton(f"Join Channel {i+1}", url=row_data[0]))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("Verify Channels", callback_data="check_membership")])
    return InlineKeyboardMarkup(keyboard)

def get_webapp_verify_keyboard():
    # Only the verify button remains.
    keyboard = [
        [InlineKeyboardButton("Verify Your Device", web_app=WebAppInfo(url=WEBAPP_URL))]
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
    expired = db_query("SELECT id, assigned_to FROM tasks WHERE status = 'assigned' AND assigned_at < ?", (cutoff,), fetchall=True)
    for tid, uid in expired:
        db_query("UPDATE tasks SET status = 'available', assigned_to = NULL, assigned_at = NULL WHERE id = ?", (tid,), commit=True)
        try: await context.bot.send_message(chat_id=uid, text="⚠️ Task expired (30m limit). Released to queue.")
        except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, username = update.effective_user.id, update.effective_user.username or "Unknown"
    
    # 1. Maintenance Mode Check (Always first)
    bot_status = db_query("SELECT value FROM config WHERE key='bot_status'", fetchone=True)[0]
    if bot_status == 'OFF' and user_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Maintenance mode.")
        return

    # 2. Database check for user state
    user = db_query("SELECT is_banned, device_verified FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    
    # 3. Check for ban
    if user and user[0] == 1:
        await update.message.reply_text("❌ Access Denied.")
        return
        
    # 4. If user not in DB, add them
    if not user:
        ref_id = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user_id else None
        db_query("INSERT INTO users (user_id, username, referred_by, device_verified) VALUES (?, ?, ?, 0)", (user_id, username, ref_id), commit=True)
        device_verified = 0
    else:
        device_verified = user[1]

    # 5. Check if verified in DB (Skip sequence if already verified)
    if device_verified == 1:
        menu_text = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)[0]
        await update.message.reply_text(menu_text, reply_markup=get_main_menu_keyboard(user_id))
        return

    # 6. Force Channel Join
    if not await check_user_joined_channels(context.bot, user_id) and user_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Join channels first:", reply_markup=get_channel_verification_keyboard())
        return

    # 7. Force Device Verification
    await update.message.reply_text(
        "🔒 *Verify Yourself To Start Bot*\n\nPlease click the button below to complete a quick device security check.", 
        parse_mode="Markdown", 
        reply_markup=get_webapp_verify_keyboard()

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "check_membership":
        if await check_user_joined_channels(context.bot, user_id):
            user = db_query("SELECT device_verified FROM users WHERE user_id = ?", (user_id,), fetchone=True)
            device_verified = user[0] if user else 0
            
            if not device_verified and user_id not in ADMIN_IDS:
                await query.message.edit_text("✅ Channels Joined!\n\n🔒 *Verify Yourself To Start Bot*\n\nPlease click the button below to complete the device security check.", parse_mode="Markdown", reply_markup=get_webapp_verify_keyboard())
            else:
                menu_text = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)[0]
                await query.message.delete()
                await context.bot.send_message(chat_id=user_id, text="✅ All Verifications Complete!\n\n" + menu_text, reply_markup=get_main_menu_keyboard(user_id))
        else: 
            await query.message.edit_text("❌ Join all channels.", reply_markup=get_channel_verification_keyboard())
        return

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
        stats_msg = f"Total users in bot :- \"{total_u}\"\n\nTotal verified users :- \"{verified_u}\"\n\nTotal withdrawal:- \"₹{total_wd}\"\n\nTotal tasks completed:- \"{total_t}\""
        await query.message.reply_text(stats_msg)
    
    elif data == "main_menu":
        menu_text = db_query("SELECT value FROM config WHERE key='menu_text'", fetchone=True)[0]
        await query.message.delete()
        await context.bot.send_message(chat_id=user_id, text=menu_text, reply_markup=get_main_menu_keyboard(user_id))
    
    elif data == "get_task":
        active = db_query("SELECT id FROM tasks WHERE assigned_to = ? AND status IN ('assigned', 'pending_approval')", (user_id,), fetchone=True)
        if active: await query.message.reply_text("⚠️ Finish active task first."); return
        task = db_query("SELECT id, task_data FROM tasks WHERE status = 'available' LIMIT 1", fetchone=True)
        if not task: await query.message.reply_text("📭 No tasks."); return
        
        tid, tdata = task
        try: t_user, t_pass = tdata.split(":")
        except: await query.message.reply_text("⚠️ Task Error."); return
        
        db_query("UPDATE tasks SET status = 'assigned', assigned_to = ?, assigned_at = ? WHERE id = ?", (user_id, datetime.now().isoformat(), tid), commit=True)
        msg = f"TASK ID :- \"{tid}\"\n\nUSERNAME :- `{t_user}`\n\nPASSWORD :- `{t_pass}`\n\nTASK TIMEOUT IN 30MINS."
        await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Submit", callback_data=f"subm_t_{tid}"), InlineKeyboardButton("❌ Cancel", callback_data=f"canc_t_{tid}")]]))

    elif data.startswith("canc_t_"):
        tid = int(data.split("_")[2])
        db_query("UPDATE tasks SET status='available', assigned_to=NULL, assigned_at=NULL WHERE id=? AND assigned_to=?", (tid, user_id), commit=True)
        await query.message.edit_text("❌ Task canceled. It is now back in the public queue.")
    
    elif data.startswith("subm_t_"):
        tid = int(data.split("_")[2])
        db_query("UPDATE tasks SET status = 'pending_approval' WHERE id = ?", (tid,), commit=True)
        await query.message.edit_text("⏳ Submitted.")
        
        t_info = db_query("SELECT assigned_to, task_data FROM tasks WHERE id=?", (tid,), fetchone=True)
        try: t_user = t_info[1].split(":")[0]
        except: t_user = "Error"
        
        adm_msg = f"TASK ID :- \"{tid}\"\n\nUSER ID :- \"{t_info[0]}\"\n\nUSERNAME :- `{t_user}`\n\nSUBMIT TIME:- \"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\""
        for admin in ADMIN_IDS:
            try: await context.bot.send_message(admin, adm_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"adm_app_t_{tid}"), InlineKeyboardButton("Reject", callback_data=f"adm_rej_t_{tid}")]]))
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
    
    elif data == "adm_bulk": context.user_data['state'] = 'ADM_WAITING_BULK'; await query.message.reply_text("Format: `u:p,u:p`")
    
    elif data == "adm_pending_tasks" and user_id in ADMIN_IDS:
        tks = db_query("SELECT id, task_data FROM tasks WHERE status='available'", fetchall=True)
        kb = [[InlineKeyboardButton("🗑️ Clear All", callback_data="adm_del_all_tasks")], [InlineKeyboardButton("🗑️ Delete Indiv", callback_data="adm_del_indiv_task")], [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        msg = "📋 Available Tasks:\n\n" + "\n".join([f"ID {t[0]}: {t[1]}" for t in tks]) if tks else "No available tasks."
        await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "adm_del_all_tasks" and user_id in ADMIN_IDS:
        db_query("DELETE FROM tasks WHERE status='available'", commit=True); await query.message.reply_text("Queue cleared.")
    
    elif data == "adm_del_indiv_task" and user_id in ADMIN_IDS:
        context.user_data['state'] = 'ADM_DEL_INDIV'; await query.message.reply_text("Enter Task ID to delete:")

    elif data == "adm_list_task_app":
        p = db_query("SELECT id, assigned_to FROM tasks WHERE status='pending_approval'", fetchall=True)
        if not p: await query.message.reply_text("No pending apps."); return
        for t in p: await query.message.reply_text(f"ID: {t[0]} by {t[1]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"adm_app_t_{t[0]}"), InlineKeyboardButton("Reject", callback_data=f"adm_rej_t_{t[0]}")]]))
    
    elif data.startswith(("adm_app_t_", "adm_rej_t_")):
        p = data.split("_"); context.user_data.update({'adm_action_task_id': int(p[3]), 'adm_action_type': p[1], 'state': 'ADM_TASK_REMARK'}); await query.message.reply_text("Remark:")
    
    elif data == "adm_list_wd":
        w = context.bot_data.get('withdrawals', {})
        if not w: await query.message.reply_text("No pending WD."); return
        for k, v in list(w.items()): await query.message.reply_text(f"ID: {k} User: {v['user_id']} Amt: {v['amount']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("App", callback_data=f"adm_app_w_{k}"), InlineKeyboardButton("Rej", callback_data=f"adm_rej_w_{k}")]]))
    
    elif data.startswith(("adm_app_w_", "adm_rej_w_")):
        p = data.split("_"); context.user_data.update({'adm_wd_id': p[3], 'adm_wd_type': p[1], 'state': 'ADM_WD_REMARK'}); await query.message.reply_text("Remark:")
    
    elif data == "adm_broadcast": context.user_data['state'] = 'ADM_BROADCAST'; await query.message.reply_text("Msg:")
    elif data == "adm_dm": context.user_data['state'] = 'ADM_DM'; await query.message.reply_text("Format: `id:msg`")
    elif data == "adm_ban": context.user_data['state'] = 'ADM_BAN'; await query.message.reply_text("ID to ban:")
    elif data == "adm_unban": context.user_data['state'] = 'ADM_UNBAN'; await query.message.reply_text("ID to unban:")
    
    elif data == "adm_tog_wd":
        c = db_query("SELECT value FROM config WHERE key='withdrawal_status'", fetchone=True)[0]
        s = 'OFF' if c == 'ON' else 'ON'; db_query("UPDATE config SET value=? WHERE key='withdrawal_status'", (s,), commit=True); await query.message.reply_text(f"WD {s}")
    elif data == "adm_tog_bot":
        c = db_query("SELECT value FROM config WHERE key='bot_status'", fetchone=True)[0]
        s = 'OFF' if c == 'ON' else 'ON'; db_query("UPDATE config SET value=? WHERE key='bot_status'", (s,), commit=True); await query.message.reply_text(f"Bot {s}")
    
    elif data == "adm_chk_bal": context.user_data['state'] = 'ADM_CHK_BAL'; await query.message.reply_text("ID:")
    elif data == "adm_mod_bal": context.user_data['state'] = 'ADM_MOD_BAL'; await query.message.reply_text("Format: `id:amt`")
    elif data == "adm_top_bal":
        t = db_query("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10", fetchall=True)
        await query.message.reply_text("\n".join([f"{i+1}) {r[0]} - ₹{r[1]:.2f}" for i, r in enumerate(t)]))
    
    elif data == "adm_chg_text": context.user_data['state'] = 'ADM_CHG_TEXT'; await query.message.reply_text("New Menu Text:")
    elif data == "adm_task_status_lookup": context.user_data['state'] = 'ADM_LOOKUP_TASK'; await query.message.reply_text("Task ID:")
    
    elif data == "adm_manage_channels":
        kb = [[InlineKeyboardButton("➕ Add", callback_data="adm_add_chan"), InlineKeyboardButton("❌ Rem", callback_data="adm_rem_chan")], [InlineKeyboardButton("📋 List", callback_data="adm_list_chan")], [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        await query.message.edit_text("Channels:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "adm_add_chan": context.user_data['state'] = 'ADM_ADD_CHAN_DATA'; await query.message.reply_text("id:link")
    elif data == "adm_rem_chan": context.user_data['state'] = 'ADM_REM_CHAN_DATA'; await query.message.reply_text("id to rem")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = context.user_data.get('state')

    if text == "📝 Get Task":
        active = db_query("SELECT id FROM tasks WHERE assigned_to = ? AND status IN ('assigned', 'pending_approval')", (user_id,), fetchone=True)
        if active: await update.message.reply_text("⚠️ Finish active task first."); return
        task = db_query("SELECT id, task_data FROM tasks WHERE status = 'available' LIMIT 1", fetchone=True)
        if not task: await update.message.reply_text("📭 No tasks."); return
        
        tid, tdata = task
        try: t_user, t_pass = tdata.split(":")
        except: await update.message.reply_text("⚠️ Task Error."); return
        
        db_query("UPDATE tasks SET status = 'assigned', assigned_to = ?, assigned_at = ? WHERE id = ?", (user_id, datetime.now().isoformat(), tid), commit=True)
        msg = f"TASK ID :- \"{tid}\"\n\nUSERNAME :- `{t_user}`\n\nPASSWORD :- `{t_pass}`\n\nTASK TIMEOUT IN 30MINS."
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Submit", callback_data=f"subm_t_{tid}"), InlineKeyboardButton("❌ Cancel", callback_data=f"canc_t_{tid}")]]))
        return

    elif text == "💰 Wallet":
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        await update.message.reply_text(f"💳 Balance: ₹{u[0]:.2f}\nUPI: `{u[1] or 'None'}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Link UPI", callback_data="add_upi")], [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]]))
        return

    elif text == "💸 Withdraw":
        wd_s = db_query("SELECT value FROM config WHERE key='withdrawal_status'", fetchone=True)[0]
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if wd_s == 'OFF': await update.message.reply_text("⚠️ WD OFF"); return
        if not u[1]: await update.message.reply_text("❌ Link UPI first"); return
        context.user_data['state'] = 'WAITING_WD_AMOUNT'; await update.message.reply_text(f"Amt (Max ₹{u[0]}):")
        return

    elif text == "👥 Refer & Earn":
        bot_me = await context.bot.get_me()
        c = db_query("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,), fetchone=True)[0]
        await update.message.reply_text(f"👥 Referrals: {c}\nLink: `t.me/{bot_me.username}?start={user_id}`", parse_mode="Markdown")
        return

    elif text == "📞 Support":
        admin_contact_url = f"tg://user?id={ADMIN_IDS[0]}"
        await update.message.reply_text(f"📞 Contact Support: {admin_contact_url}")
        return

    elif text == "⚙️ Admin Panel" and user_id in ADMIN_IDS:
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
        await update.message.reply_text("⚙️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(kbd))
        return

    if not state: return
    context.user_data['state'] = None

    if state == 'WAITING_UPI': db_query("UPDATE users SET upi_id=? WHERE user_id=?", (text, user_id), commit=True); await update.message.reply_text("UPI Linked.")
    
    elif state == 'WAITING_WD_AMOUNT':
        try: amt = float(text)
        except: return
        u = db_query("SELECT balance, upi_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if 0 < amt <= u[0]:
            if 'withdrawals' not in context.bot_data: context.bot_data['withdrawals'] = {}
            wid = str(int(datetime.now().timestamp()))
            context.bot_data['withdrawals'][wid] = {'user_id': user_id, 'amount': amt, 'upi': u[1], 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            await update.message.reply_text("WD Requested.")
            
            adm_wd_msg = f"USER ID :- \"{user_id}\"\n\nUPI ID :- `{u[1]}`\n\nAMOUNT:- \"{amt}\"\n\nWITHDRAWAL TIME:- \"{context.bot_data['withdrawals'][wid]['time']}\""
            for a in ADMIN_IDS:
                try: await context.bot.send_message(a, adm_wd_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"adm_app_w_{wid}"), InlineKeyboardButton("Reject", callback_data=f"adm_rej_w_{wid}")]]))
                except: pass

    elif state == 'ADM_LOOKUP_TASK':
        if not text.isdigit(): return
        t = db_query("SELECT status, assigned_to, assigned_at, task_data FROM tasks WHERE id=?", (int(text),), fetchone=True)
        if t:
            status_db, assigned_to_db, assigned_at_db, task_data_db = t
            status_map = {
                'available': 'NOT ASSIGNED',
                'assigned': 'PENDING',
                'pending_approval': 'PENDING',
                'completed': 'COMPLETED'
            }
            display_status = status_map.get(status_db, 'NOT ASSIGNED')
            try: task_user = task_data_db.split(":")[0]
            except: task_user = "N/A"
            tl = "N/A"
            if status_db == 'assigned' and assigned_at_db:
                df = (datetime.fromisoformat(assigned_at_db) + timedelta(minutes=30)) - datetime.now()
                tl = f"{int(df.total_seconds()//60)}m {int(df.total_seconds()%60)}s" if df.total_seconds() > 0 else "Expired"
            lookup_msg = (
                f"TASK STATUS:- \"{display_status}\"\n\n"
                f"TASK USERNAME:- `{task_user}`\n\n"
                f"USER ID:- \"{assigned_to_db if assigned_to_db else 'N/A'}\"\n\n"
                f"TIME :- \"{assigned_at_db if assigned_at_db else 'N/A'}\"\n\n"
                f"TIMEOUT LEFT :- \"{tl}\""
            )
            await update.message.reply_text(lookup_msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Task ID not found.")

    elif state == 'ADM_ADD_CHAN_DATA':
        if ":" in text: 
            cid, lnk = text.split(":", 1)
            db_query("INSERT OR REPLACE INTO channels (chat_id, invite_link) VALUES (?,?)", (cid.strip(), lnk.strip()), commit=True); await update.message.reply_text("Added.")
    
    elif state == 'ADM_REM_CHAN_DATA': db_query("DELETE FROM channels WHERE chat_id=?", (text,), commit=True); await update.message.reply_text("Deleted.")
    
    elif state == 'ADM_DEL_INDIV':
        if text.isdigit(): db_query("DELETE FROM tasks WHERE id=? AND status='available'", (int(text),), commit=True); await update.message.reply_text("Task deleted.")
        
    elif state == 'ADM_WAITING_BULK':
        for i in text.split(","): 
            if ":" in i: db_query("INSERT INTO tasks (task_data) VALUES (?)", (i.strip(),), commit=True)
        await update.message.reply_text("Bulk added.")
    
    elif state == 'ADM_TASK_REMARK':
        tid, act = context.user_data['adm_action_task_id'], context.user_data['adm_action_type']
        uid = db_query("SELECT assigned_to FROM tasks WHERE id=?", (tid,), fetchone=True)[0]
        status = "APPROVED" if act == 'app' else "REJECTED"
        if act == 'app':
            db_query("UPDATE tasks SET status='completed' WHERE id=?", (tid,), commit=True)
            db_query("UPDATE users SET balance=balance+15 WHERE user_id=?", (uid,), commit=True)
        else:
            db_query("UPDATE tasks SET status='available', assigned_to=NULL, assigned_at=NULL WHERE id=?", (tid,), commit=True)
        
        await context.bot.send_message(uid, f"TASK ID :- \"{tid}\"\n\nSTATUS:- \"{status}\"\n\nREMARKS:- \"{text}\"")
        await update.message.reply_text(f"Task {tid} processed as {status}.")

    elif state == 'ADM_WD_REMARK':
        wid, act = context.user_data['adm_wd_id'], context.user_data['adm_wd_type']
        wd = context.bot_data['withdrawals'].pop(wid, None)
        if wd:
            status = "APPROVED" if act == 'app' else "REJECTED"
            if act == 'app':
                db_query("UPDATE users SET balance=balance-? WHERE user_id=?", (wd['amount'], wd['user_id']), commit=True)
                cur_total = float(db_query("SELECT value FROM config WHERE key='total_wd_processed'", fetchone=True)[0])
                db_query("UPDATE config SET value=? WHERE key='total_wd_processed'", (str(cur_total + wd['amount']),), commit=True)
            
            wd_msg = f"WITHDRAWAL STATUS:- \"{status}\"\n\nWITHDRAWAL TIME :- \"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\"\n\nREMARKS :- \"{text}\""
            await context.bot.send_message(wd['user_id'], wd_msg)
            await update.message.reply_text(f"Withdrawal {wid} processed as {status}.")

    elif state == 'ADM_BROADCAST':
        for u in db_query("SELECT user_id FROM users", fetchall=True):
            try: await context.bot.send_message(u[0], f"📢 **Announcement**\n\n{text}", parse_mode="Markdown")
            except: pass
    elif state == 'ADM_DM' and ":" in text:
        target, msg = text.split(":", 1)
        try: await context.bot.send_message(int(target), f"💬 **Admin Msg:** {msg}"); await update.message.reply_text("Sent.")
        except: await update.message.reply_text("Failed.")
    elif state == 'ADM_BAN': db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (int(text),), commit=True); await update.message.reply_text("Banned.")
    elif state == 'ADM_UNBAN': db_query("UPDATE users SET is_banned=0 WHERE user_id=?", (int(text),), commit=True); await update.message.reply_text("Unbanned.")
    elif state == 'ADM_CHK_BAL':
        b = db_query("SELECT balance FROM users WHERE user_id=?", (int(text),), fetchone=True)
        await update.message.reply_text(f"Bal: ₹{b[0] if b else 'N/A'}")
    elif state == 'ADM_MOD_BAL' and ":" in text:
        target, amt = text.split(":", 1)
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (float(amt), int(target)), commit=True); await update.message.reply_text("Updated.")
    elif state == 'ADM_CHG_TEXT': db_query("UPDATE config SET value=? WHERE key='menu_text'", (text,), commit=True); await update.message.reply_text("Menu Updated.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(task_timeout_monitor, interval=60)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == '__main__': main()

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

    # =====================================================
    # MAIN MENU
    # =====================================================

    if data == "main_menu":

        res = db_query(
            "SELECT value FROM config WHERE key='menu_text'",
            fetchone=True
        )

        await query.message.edit_text(
            res[0],
            reply_markup=get_main_menu_keyboard(user_id)
        )

    # =====================================================
    # ADMIN PANEL
    # =====================================================

    elif data == "admin_panel" and user_id in ADMIN_IDS:

        kbd = [
            [InlineKeyboardButton("📤 Bulk Upload", callback_data="adm_bulk"),
             InlineKeyboardButton("📋 Tasks Queue", callback_data="adm_pending_tasks")],

            [InlineKeyboardButton("📥 Task Approvals", callback_data="adm_list_task_app"),
             InlineKeyboardButton("🏧 WD Requests", callback_data="adm_list_wd")],

            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"),
             InlineKeyboardButton("💬 DM User", callback_data="adm_dm")],

            [InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"),
             InlineKeyboardButton("🔓 Unban User", callback_data="adm_unban")],

            [InlineKeyboardButton("Toggle WD", callback_data="adm_tog_wd"),
             InlineKeyboardButton("Toggle Bot", callback_data="adm_tog_bot")],

            [InlineKeyboardButton("🪙 Check Balance", callback_data="adm_chk_bal"),
             InlineKeyboardButton("💳 Mod Balance", callback_data="adm_mod_bal")],

            [InlineKeyboardButton("🏆 Top 10 Bal", callback_data="adm_top_bal"),
             InlineKeyboardButton("📝 Menu Text", callback_data="adm_chg_text")],

            [InlineKeyboardButton("📢 Manage Channels", callback_data="adm_manage_channels"),
             InlineKeyboardButton("🔍 Task Lookup", callback_data="adm_task_status_lookup")],

            [InlineKeyboardButton("📊 Bot Stats", callback_data="adm_stats"),
             InlineKeyboardButton("💾 DB Backup", callback_data="adm_backup")],

            [InlineKeyboardButton("❌ Close", callback_data="main_menu")]
        ]

        await query.message.edit_text(
            "⚙️ ADMIN PANEL",
            reply_markup=InlineKeyboardMarkup(kbd)
        )

    # =====================================================
    # USER BUTTONS
    # =====================================================

        elif data == "wallet":
        u = db_query(
            "SELECT balance, upi_id FROM users WHERE user_id=?",
            (user_id,),
            fetchone=True
        )

        await query.message.edit_text(
            f"💳 Balance: ₹{u[0]:.2f}\nUPI: `{u[1] or 'Not Linked'}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Link UPI", callback_data="add_upi")],
                [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
                [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
            ])
        )

    elif data == "add_upi":
        context.user_data['state'] = 'WAITING_UPI'
        await query.message.reply_text("Send your UPI ID:")

    elif data == "withdraw":
        wd_status = db_query(
            "SELECT value FROM config WHERE key='withdrawal_status'",
            fetchone=True
        )

        if wd_status and wd_status[0] == "OFF":
            await query.message.reply_text("❌ Withdrawal is currently disabled.")
            return

        u = db_query(
            "SELECT balance, upi_id FROM users WHERE user_id=?",
            (user_id,),
            fetchone=True
        )

        if not u:
            await query.message.reply_text("❌ User data not found.")
            return

        balance = float(u[0])
        upi = u[1]

        if not upi or upi.strip() == "":
            await query.message.reply_text(
                "❌ Please link your UPI ID first from Wallet section."
            )
            return

        if balance <= 0:
            await query.message.reply_text("❌ Insufficient balance.")
            return

        context.user_data['state'] = 'WAITING_WD_AMOUNT'

        await query.message.reply_text(
            f"💸 Enter withdrawal amount.\n\nAvailable Balance: ₹{balance}"
        )
    elif data == "refer_earn":

        bot_info = await context.bot.get_me()

        link = f"https://t.me/{bot_info.username}?start={user_id}"

        refs = db_query(
            "SELECT COUNT(*) FROM users WHERE referred_by=?",
            (user_id,),
            fetchone=True
        )[0]

        await query.message.edit_text(
            f"👥 Referrals: {refs}\n\n🔗 Link:\n`{link}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
            ])
        )

    # =====================================================
    # GET TASK
    # =====================================================

    elif data == "get_task":

        active = db_query(
            "SELECT id FROM tasks WHERE assigned_to=? AND status IN ('assigned','pending_approval')",
            (user_id,),
            fetchone=True
        )

        if active:

            await query.message.reply_text(
                "⚠️ Complete current task first."
            )

            return

        task = db_query(
            "SELECT id, task_data FROM tasks WHERE status='available' LIMIT 1",
            fetchone=True
        )

        if not task:

            await query.message.reply_text(
                "❌ No Tasks Available"
            )

            return

        tid, tdata = task

        db_query(
            "UPDATE tasks SET status='assigned', assigned_to=?, assigned_at=? WHERE id=?",
            (user_id, datetime.now().isoformat(), tid),
            commit=True
        )

        kb = [[
            InlineKeyboardButton(
                "✅ Submit",
                callback_data=f"subm_t_{tid}"
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"canc_t_{tid}"
            )
        ]]

        await query.message.reply_text(
            f"📝 TASK ID: {tid}\n\n`{tdata}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data.startswith("subm_t_"):

        tid = int(data.split("_")[2])

        db_query(
            "UPDATE tasks SET status='pending_approval' WHERE id=?",
            (tid,),
            commit=True
        )

        await query.message.edit_text(
            "✅ Submitted For Approval"
        )

    elif data.startswith("canc_t_"):

        tid = int(data.split("_")[2])

        db_query(
            "UPDATE tasks SET status='available', assigned_to=NULL, assigned_at=NULL WHERE id=?",
            (tid,),
            commit=True
        )

        await query.message.edit_text(
            "❌ Task Cancelled"
        )

    # =====================================================
    # ADMIN FEATURES
    # =====================================================

    elif data == "adm_bulk":

        context.user_data["state"] = "ADM_WAITING_BULK"

        await query.message.reply_text(
            "Send:\nuser:pass,user:pass"
        )

    elif data == "adm_broadcast":

        context.user_data["state"] = "ADM_BROADCAST"

        await query.message.reply_text(
            "Send broadcast message:"
        )

    elif data == "adm_dm":

        context.user_data["state"] = "ADM_DM_STATE"

        await query.message.reply_text(
            "Send:\nid:message"
        )

    elif data == "adm_ban":

        context.user_data["state"] = "ADM_BAN_STATE"

        await query.message.reply_text(
            "Send User ID:"
        )

    elif data == "adm_unban":

        context.user_data["state"] = "ADM_UNBAN_STATE"

        await query.message.reply_text(
            "Send User ID:"
        )

    elif data == "adm_chk_bal":

        context.user_data["state"] = "ADM_CHK_BAL_STATE"

        await query.message.reply_text(
            "Send User ID:"
        )

    elif data == "adm_mod_bal":

        context.user_data["state"] = "ADM_MOD_BAL_STATE"

        await query.message.reply_text(
            "Send:\nid:amount"
        )

    elif data == "adm_chg_text":

        context.user_data["state"] = "ADM_CHG_TEXT"

        await query.message.reply_text(
            "Send new menu text:"
        )

    elif data == "adm_task_status_lookup":

        context.user_data["state"] = "ADM_TASK_LOOKUP"

        await query.message.reply_text(
            "Send Task ID:"
        )
    # =========================
    # TASK QUEUE
    # =========================

    elif data == "adm_pending_tasks" and user_id in ADMIN_IDS:

        tasks = db_query(
            "SELECT id, task_data FROM tasks WHERE status='available'",
            fetchall=True
        )

        if not tasks:

            await query.message.reply_text(
                "📭 No pending tasks."
            )

            return

        msg = "📋 TASK QUEUE\n\n"

        for t in tasks:

            msg += f"ID: {t[0]}\n{t[1]}\n\n"

        await query.message.reply_text(msg)

    # =========================
    # TASK APPROVALS
    # =========================

    elif data == "adm_list_task_app" and user_id in ADMIN_IDS:

        tasks = db_query(
            "SELECT id, assigned_to FROM tasks WHERE status='pending_approval'",
            fetchall=True
        )

        if not tasks:

            await query.message.reply_text(
                "📭 No pending approvals."
            )

            return

        for t in tasks:

            kb = [[

                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"adm_app_t_{t[0]}"
                ),

                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"adm_rej_t_{t[0]}"
                )

            ]]

            await query.message.reply_text(
                f"📝 Task ID: {t[0]}\n👤 User: {t[1]}",
                reply_markup=InlineKeyboardMarkup(kb)
            )

    # =========================
    # WITHDRAWAL REQUESTS
    # =========================

    elif data == "adm_list_wd" and user_id in ADMIN_IDS:

        wds = context.bot_data.get("withdrawals", {})

        if not wds:

            await query.message.reply_text(
                "📭 No withdrawal requests."
            )

            return

        for wid, wd in wds.items():

            kb = [[

                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"adm_app_w_{wid}"
                ),

                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"adm_rej_w_{wid}"
                )

            ]]

            await query.message.reply_text(
                f"💸 WD ID: {wid}\n👤 User: {wd['user_id']}\n💰 Amount: ₹{wd['amount']}",
                reply_markup=InlineKeyboardMarkup(kb)
            )

    # =========================
    # TOGGLE WD
    # =========================

    elif data == "adm_tog_wd" and user_id in ADMIN_IDS:

        cur = db_query(
            "SELECT value FROM config WHERE key='withdrawal_status'",
            fetchone=True
        )[0]

        new = "OFF" if cur == "ON" else "ON"

        db_query(
            "UPDATE config SET value=? WHERE key='withdrawal_status'",
            (new,),
            commit=True
        )

        await query.message.reply_text(
            f"✅ Withdrawal Status: {new}"
        )

    # =========================
    # TOGGLE BOT
    # =========================

    elif data == "adm_tog_bot" and user_id in ADMIN_IDS:

        cur = db_query(
            "SELECT value FROM config WHERE key='bot_status'",
            fetchone=True
        )[0]

        new = "OFF" if cur == "ON" else "ON"

        db_query(
            "UPDATE config SET value=? WHERE key='bot_status'",
            (new,),
            commit=True
        )

        await query.message.reply_text(
            f"✅ Bot Status: {new}"
        )

    # =========================
    # TOP BALANCE
    # =========================

    elif data == "adm_top_bal" and user_id in ADMIN_IDS:

        top = db_query(
            "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10",
            fetchall=True
        )

        if not top:

            await query.message.reply_text(
                "No users."
            )

            return

        txt = "🏆 TOP 10 BALANCES\n\n"

        for i, row in enumerate(top, start=1):

            txt += f"{i}. {row[0]} → ₹{row[1]}\n"

        await query.message.reply_text(txt)

    # =========================
    # BOT STATS
    # =========================

    elif data == "adm_stats" and user_id in ADMIN_IDS:

        total_users = db_query(
            "SELECT COUNT(*) FROM users",
            fetchone=True
        )[0]

        total_tasks = db_query(
            "SELECT COUNT(*) FROM tasks WHERE status='completed'",
            fetchone=True
        )[0]

        total_wd = db_query(
            "SELECT value FROM config WHERE key='total_wd_processed'",
            fetchone=True
        )[0]

        txt = (
            f"📊 BOT STATS\n\n"
            f"👥 Users: {total_users}\n"
            f"✅ Completed Tasks: {total_tasks}\n"
            f"💸 Withdrawals: ₹{total_wd}"
        )

        await query.message.reply_text(txt)

    elif data == "adm_manage_channels":

        kb = [
            [InlineKeyboardButton("➕ Add", callback_data="adm_add_chan"),
             InlineKeyboardButton("❌ Remove", callback_data="adm_rem_chan")],

            [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]
        ]

        await query.message.edit_text(
            "📢 Manage Channels",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data == "adm_add_chan":

        context.user_data["state"] = "ADM_ADD_CHAN"

        await query.message.reply_text(
            "Send:\nchannel_id:invite_link"
        )

    elif data == "adm_rem_chan":

        context.user_data["state"] = "ADM_REM_CHAN"

        await query.message.reply_text(
            "Send Channel ID:"
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, text, state = update.effective_user.id, update.message.text.strip(), context.user_data.get('state')
    if not state: return
    context.user_data['state'] = None

    # Admin Processing
    if state == 'ADM_WAITING_BULK':
        for pair in text.split(","):
            if ":" in pair: db_query("INSERT INTO tasks (task_data) VALUES (?)", (pair.strip(),), commit=True)
        await update.message.reply_text("Bulk uploaded.")

    elif state == 'ADM_TASK_REM':
        tid, act = context.user_data['adm_act_tid'], context.user_data['adm_act_type']
        uid = db_query("SELECT assigned_to FROM tasks WHERE id=?", (tid,), fetchone=True)[0]
        if act == 'app':
            db_query("UPDATE tasks SET status='completed' WHERE id=?", (tid,), commit=True)
            db_query("UPDATE users SET balance=balance+15 WHERE user_id=?", (uid,), commit=True) # Adding 15 INR reward
            await context.bot.send_message(uid, f"✅ Task {tid} Approved! ₹15 added. Remark: {text}")
        else:
            db_query("UPDATE tasks SET status='available', assigned_to=NULL, assigned_at=NULL WHERE id=?", (tid,), commit=True)
            await context.bot.send_message(uid, f"❌ Task {tid} Rejected. Remark: {text}")
        await update.message.reply_text("Task processed.")

    elif state == 'ADM_WD_REM':
        wid, act = context.user_data['adm_wd_id'], context.user_data['adm_wd_type']
        wd = context.bot_data.get('withdrawals', {}).pop(wid, None)
        if wd:
            if act == 'app':
                db_query("UPDATE users SET balance=balance-? WHERE user_id=?", (wd['amount'], wd['user_id']), commit=True)
                await context.bot.send_message(wd['user_id'], f"✅ Withdrawal of ₹{wd['amount']} Approved! Remark: {text}")
            else:
                await context.bot.send_message(wd['user_id'], f"❌ Withdrawal of ₹{wd['amount']} Rejected. Remark: {text}")
        await update.message.reply_text("Withdrawal processed.")

    elif state == 'ADM_DM_STATE' and ":" in text:
        target, msg = text.split(":", 1)
        try: await context.bot.send_message(int(target), f"💬 **Admin Message:**\n{msg}"); await update.message.reply_text("Sent.")
        except: await update.message.reply_text("Failed.")

    elif state == 'ADM_BAN_STATE':
        db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (int(text),), commit=True); await update.message.reply_text("Banned.")

    elif state == 'ADM_MOD_BAL_STATE' and ":" in text:
        target, amt = text.split(":", 1)
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (float(amt), int(target)), commit=True); await update.message.reply_text("Updated.")

    # Standard states (UPI, WD, etc.)
    elif state == 'WAITING_WD':

    try:
        amt = float(text)

    except:

        await update.message.reply_text(
            "❌ Invalid amount."
        )

        return

    u = db_query(
        "SELECT balance, upi_id FROM users WHERE user_id=?",
        (user_id,),
        fetchone=True
    )

    if not u:

        return

    balance = u[0]
    upi = u[1]

    if amt <= 0 or amt > balance:

        await update.message.reply_text(
            "❌ Invalid amount."
        )

        return

    if "withdrawals" not in context.bot_data:
        context.bot_data["withdrawals"] = {}

    wid = str(int(datetime.now().timestamp()))

    context.bot_data["withdrawals"][wid] = {
        "user_id": user_id,
        "amount": amt,
        "upi": upi
    }

    await update.message.reply_text(
        "✅ Withdrawal request submitted."
    )

    for admin in ADMIN_IDS:

        try:

            kb = [[

                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"adm_app_w_{wid}"
                ),

                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"adm_rej_w_{wid}"
                )

            ]]

            await context.bot.send_message(
                admin,
                f"💸 New Withdrawal Request\n\n👤 User: {user_id}\n💰 Amount: ₹{amt}\n🏦 UPI: {upi}",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        except:
            pass
            
    elif state == 'WAITING_UPI':
        db_query("UPDATE users SET upi_id=? WHERE user_id=?", (text, user_id), commit=True); await update.message.reply_text("UPI Linked.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(task_timeout_monitor, interval=60)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == '__main__': main()

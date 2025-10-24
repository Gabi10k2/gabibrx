import sqlite3
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Bot as TelegramBot
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

from config import BOT_TOKEN, ADMIN_BOT_TOKEN, ADMIN_CHAT_ID, CONFIG, SERVICES, TIMEZONE

TZ = ZoneInfo(TIMEZONE)
DB_PATH = "appointments.db"

# ---------------- DB ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        client_name TEXT,
        client_phone TEXT,
        service TEXT,
        price INTEGER,
        start_ts TEXT,
        end_ts TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def add_appointment(chat_id, name, phone, service, price, start_ts, end_ts):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO appointments (chat_id, client_name, client_phone, service, price, start_ts, end_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (chat_id, name, phone, service, price, start_ts.isoformat(), end_ts.isoformat()))
    conn.commit()
    conn.close()

def get_appointments_on(day: date) -> List[Tuple]:
    start = datetime.combine(day, time.min).replace(tzinfo=TZ)
    end = datetime.combine(day, time.max).replace(tzinfo=TZ)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM appointments WHERE start_ts BETWEEN ? AND ? ORDER BY start_ts", (start.isoformat(), end.isoformat()))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_user_appointments(chat_id: int) -> List[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, service, price, start_ts, end_ts FROM appointments WHERE chat_id = ? ORDER BY start_ts", (chat_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def delete_appointment_by_id(app_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM appointments WHERE id = ?", (app_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0

# ---------------- Slots ----------------
def is_weekend(d: date) -> bool:
    return d.weekday() >= 5

def work_hours_for_day(d: date):
    if is_weekend(d):
        return datetime.strptime(CONFIG["weekend_start"], "%H:%M").time(), datetime.strptime(CONFIG["weekend_end"], "%H:%M").time()
    else:
        return datetime.strptime(CONFIG["weekday_start"], "%H:%M").time(), datetime.strptime(CONFIG["weekday_end"], "%H:%M").time()

def overlaps(a_start, a_end, b_start, b_end):
    return max(a_start, b_start) < min(a_end, b_end)

def generate_slots_for_day(d: date, duration_minutes: int):
    start_t, end_t = work_hours_for_day(d)
    start_dt = datetime.combine(d, start_t).replace(tzinfo=TZ)
    end_dt = datetime.combine(d, end_t).replace(tzinfo=TZ)
    step = timedelta(minutes=CONFIG["slot_minutes"])
    duration = timedelta(minutes=duration_minutes)

    slots = []
    cur = start_dt
    while cur + duration <= end_dt + timedelta(seconds=1):
        slots.append((cur, cur + duration))
        cur += step
    return slots

def available_slots_for_day(d: date, duration_minutes: int):
    all_slots = generate_slots_for_day(d, duration_minutes)
    booked = get_appointments_on(d)
    booked_intervals = [(datetime.fromisoformat(row[6]).replace(tzinfo=TZ), datetime.fromisoformat(row[7]).replace(tzinfo=TZ)) for row in booked]
    free = []
    now = datetime.now(TZ)
    for s, e in all_slots:
        if s < now: continue
        if any(overlaps(s, e, b_start, b_end) for b_start, b_end in booked_intervals):
            continue
        free.append((s, e))
    return free

# ---------------- Notificări admin ----------------
async def notify_admin(client_name, client_phone, svc, price, start_dt, end_dt):
    bot = TelegramBot(token=ADMIN_BOT_TOKEN)
    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(
            f"📢 *Nouă programare!*\n\n"
            f"👤 {client_name}\n"
            f"📞 {client_phone}\n"
            f"✂️ {svc} – {price} lei\n"
            f"📅 {start_dt.strftime('%d %b %Y')}\n"
            f"⏰ {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
        ),
        parse_mode="Markdown"
    )

# ---------------- Handlers ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [KeyboardButton("Fă o programare")],
        [KeyboardButton("Programările mele")]
    ]
    await update.message.reply_text("Bun venit! Alege ce vrei să faci:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def book_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for svc, info in SERVICES.items():
        keyboard.append([InlineKeyboardButton(f"{svc} ({info['duration']} min – {info['price']} lei)", callback_data=f"svc|{svc}")])
    keyboard.append([InlineKeyboardButton("Anulează", callback_data="cancel")])
    await update.message.reply_text("Alege serviciul:", reply_markup=InlineKeyboardMarkup(keyboard))

# Callback query pentru servicii/date/ore
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        await query.edit_message_text("Acțiune anulată.")
        return

    parts = data.split("|")

    # Select serviciu
    if parts[0] == "svc":
        svc = parts[1]
        context.user_data["pending_service"] = svc
        # cerem nume și telefon
        await query.edit_message_text("Scrie-ți numele complet:")
        return

    # Select dată
    elif parts[0] == "date":
        sel_date = date.fromisoformat(parts[1])
        svc = context.user_data.get("pending_service")
        duration = SERVICES[svc]["duration"]
        free_slots = available_slots_for_day(sel_date, duration)
        if not free_slots:
            await query.edit_message_text("Ne pare rău, nu sunt ore libere în ziua aleasă.")
            return
        keyboard = [[InlineKeyboardButton(s.strftime("%H:%M"), callback_data=f"time|{s.isoformat()}")] for s, e in free_slots]
        keyboard.append([InlineKeyboardButton("Înapoi", callback_data=f"svc|{svc}")])
        await query.edit_message_text(f"Alege ora pentru {sel_date.isoformat()} (serviciu: {svc}):", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Select ora
    elif parts[0] == "time":
        start_dt = datetime.fromisoformat(parts[1]).replace(tzinfo=TZ)
        svc = context.user_data.get("pending_service")
        phone = context.user_data.get("client_phone")
        name = context.user_data.get("client_name")
        if not (svc and phone and name):
            await query.edit_message_text("Eroare: lipsește informația clientului. Reîncearcă.")
            return
        duration = SERVICES[svc]["duration"]
        price = SERVICES[svc]["price"]
        end_dt = start_dt + timedelta(minutes=duration)
        add_appointment(update.effective_user.id, name, phone, svc, price, start_dt, end_dt)
        await notify_admin(name, phone, svc, price, start_dt, end_dt)
        await query.edit_message_text(
            f"✅ Programare confirmată!\n\n"
            f"👤 {name}\n"
            f"📞 {phone}\n"
            f"✂️ {svc} – {price} lei\n"
            f"📅 {start_dt.strftime('%d %b %Y')}\n"
            f"⏰ {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}\n\n"
            f"Mulțumim! Te așteptăm la GabiBRX."
        )

# Mesaje text pentru nume/telefon
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "pending_service" in context.user_data:
        if "client_name" not in context.user_data:
            context.user_data["client_name"] = update.message.text
            await update.message.reply_text("Acum scrie-ți numărul de telefon:")
            return
        elif "client_phone" not in context.user_data:
            context.user_data["client_phone"] = update.message.text
            # acum afișăm zilele disponibile
            svc = context.user_data["pending_service"]
            keyboard = []
            today = datetime.now(TZ).date()
            for i in range(CONFIG["days_ahead"]):
                d = today + timedelta(days=i)
                start_t, end_t = work_hours_for_day(d)
                if start_t >= end_t:
                    continue
                keyboard.append([InlineKeyboardButton(d.strftime("%a %d %b"), callback_data=f"date|{d.isoformat()}")])
            keyboard.append([InlineKeyboardButton("Anulează", callback_data="cancel")])
            await update.message.reply_text("Alege ziua:", reply_markup=InlineKeyboardMarkup(keyboard))
            return
    text = update.message.text.lower()
    if "programare" in text:
        await book_handler(update, context)
    elif "programările mele" in text:
        await my_programari_handler(update, context)
    else:
        await update.message.reply_text("Comandă necunoscută. Folosește /start sau apasă butoanele.")

# Vizualizare programări user
async def my_programari_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    rows = get_user_appointments(chat_id)
    if not rows:
        await update.message.reply_text("Nu ai programări înregistrate.")
        return
    text_lines = ["Programările tale:"]
    for row in rows:
        app_id, service, price, start_ts, end_ts = row
        start_dt = datetime.fromisoformat(start_ts).replace(tzinfo=TZ)
        end_dt = datetime.fromisoformat(end_ts).replace(tzinfo=TZ)
        text_lines.append(f"ID {app_id} — {service} – {price} lei — {start_dt.strftime('%d %b %H:%M')} - {end_dt.strftime('%H:%M')}")
    text_lines.append("\nPentru anulare: /cancel <ID>")
    await update.message.reply_text("\n".join(text_lines))

# Cancel programare
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Folosire: /cancel <ID_programare>")
        return
    try:
        app_id = int(args[0])
    except:
        await update.message.reply_text("ID invalid.")
        return
    chat_id = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM appointments WHERE id = ?", (app_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await update.message.reply_text("Nu există programarea cu acest ID.")
        return
    owner_chat_id = row[0]
    if chat_id != owner_chat_id and chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Nu ai permisiunea să anulezi această programare.")
        return
    ok = delete_appointment_by_id(app_id)
    await update.message.reply_text("Programarea a fost anulată." if ok else "Eroare la anulare.")

# ---------------- Admin panel ----------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Acces interzis.")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, client_name, client_phone, service, price, start_ts, end_ts FROM appointments ORDER BY start_ts")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Nu există programări viitoare.")
        return
    for r in rows:
        app_id, name, phone, svc, price, s, e = r
        s_dt = datetime.fromisoformat(s)
        e_dt = datetime.fromisoformat(e)
        text = (
            f"ID {app_id}\n"
            f"👤 {name}\n"
            f"📞 {phone}\n"
            f"✂️ {svc} – {price} lei\n"
            f"📅 {s_dt.strftime('%d %b %Y')}\n"
            f"⏰ {s_dt.strftime('%H:%M')} - {e_dt.strftime('%H:%M')}"
        )
        keyboard = [[InlineKeyboardButton("🗑️ Șterge programarea", callback_data=f"admin_cancel|{app_id}")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    if data[0] == "admin_cancel":
        app_id = int(data[1])
        delete_appointment_by_id(app_id)
        await query.edit_message_text("✅ Programarea a fost ștearsă de admin.")

# ---------------- Main ----------------
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("book", book_handler))
    app.add_handler(CommandHandler("programari", my_programari_handler))
    app.add_handler(CommandHandler("mybookings", my_programari_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_cancel\\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Bot pornit...")
    app.run_polling()

if __name__ == "__main__":
    main()

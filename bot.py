import sqlite3
from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
from config import BOT_TOKEN, ADMIN_CHAT_ID, CONFIG, SERVICES, TIMEZONE

TZ = ZoneInfo(TIMEZONE)
DB_PATH = "appointments.db"

# --- DB helpers ---
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

def get_appointments_on(day):
    start = datetime.combine(day, time.min).replace(tzinfo=TZ)
    end = datetime.combine(day, time.max).replace(tzinfo=TZ)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, chat_id, client_name, client_phone, service, price, start_ts, end_ts
        FROM appointments
        WHERE start_ts BETWEEN ? AND ?
        ORDER BY start_ts
    """, (start.isoformat(), end.isoformat()))
    rows = cur.fetchall()
    conn.close()
    return rows

# --- Helpers programare ---
def is_weekend(d: date) -> bool:
    return d.weekday() >= 5

def work_hours_for_day(d: date):
    if is_weekend(d):
        start_t = datetime.strptime(CONFIG["weekend_start"], "%H:%M").time()
        end_t = datetime.strptime(CONFIG["weekend_end"], "%H:%M").time()
    else:
        start_t = datetime.strptime(CONFIG["weekday_start"], "%H:%M").time()
        end_t = datetime.strptime(CONFIG["weekday_end"], "%H:%M").time()
    return start_t, end_t

def generate_slots_for_day(d: date, duration_minutes: int):
    start_t, end_t = work_hours_for_day(d)
    start_dt = datetime.combine(d, start_t).replace(tzinfo=TZ)
    end_dt = datetime.combine(d, end_t).replace(tzinfo=TZ)
    step = timedelta(minutes=CONFIG["slot_minutes"])
    duration = timedelta(minutes=duration_minutes)
    slots = []
    cur = start_dt
    while cur + duration <= end_dt:
        slots.append((cur, cur + duration))
        cur += step
    return slots

def available_slots_for_day(d: date, duration_minutes: int):
    all_slots = generate_slots_for_day(d, duration_minutes)
    booked = get_appointments_on(d)
    booked_intervals = [(datetime.fromisoformat(r[6]).replace(tzinfo=TZ), datetime.fromisoformat(r[7]).replace(tzinfo=TZ)) for r in booked]
    free = []
    now = datetime.now(TZ)
    for s, e in all_slots:
        if s < now:
            continue
        conflict = False
        for b_start, b_end in booked_intervals:
            if max(s, b_start) < min(e, b_end):
                conflict = True
                break
        if not conflict:
            free.append((s, e))
    return free

# --- Bot Handlers ---
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("Fă o programare")],
          [KeyboardButton("Programările mele")]]
    await update.message.reply_text(
        "Bun venit! Ce dorești să faci?",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if "programare" in text:
        await ask_name(update, context)
    elif "programările mele" in text:
        await my_programari_handler(update, context)
    else:
        await update.message.reply_text("Folosește meniul sau /start.")

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Care este numele tău complet?")
    context.user_data["step"] = "name"

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Care este numărul tău de telefon?")
    context.user_data["step"] = "phone"

async def ask_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"{svc} – {SERVICES[svc][1]} lei", callback_data=f"svc|{svc}")] for svc in SERVICES]
    keyboard.append([InlineKeyboardButton("Anulează", callback_data="cancel")])
    await update.message.reply_text("Alege serviciul:", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data["step"] = "service"

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        await query.edit_message_text("Acțiune anulată.")
        context.user_data.clear()
        return

    step = context.user_data.get("step")
    if data.startswith("svc|") and step == "service":
        svc = data.split("|")[1]
        context.user_data["service"] = svc
        context.user_data["price"] = SERVICES[svc][1]
        # Afișăm zile
        keyboard = []
        today = datetime.now(TZ).date()
        for i in range(CONFIG["days_ahead"]):
            d = today + timedelta(days=i)
            start_t, end_t = work_hours_for_day(d)
            if start_t >= end_t:
                continue
            label = d.strftime("%a %d %b")
            keyboard.append([InlineKeyboardButton(label, callback_data=f"date|{d.isoformat()}")])
        keyboard.append([InlineKeyboardButton("Anulează", callback_data="cancel")])
        await query.edit_message_text("Alege ziua:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["step"] = "date"

    elif data.startswith("date|") and step == "date":
        sel_date = date.fromisoformat(data.split("|")[1])
        context.user_data["date"] = sel_date
        svc = context.user_data["service"]
        duration = SERVICES[svc][0]
        free_slots = available_slots_for_day(sel_date, duration)
        if not free_slots:
            await query.edit_message_text("Nu sunt ore libere în ziua aleasă.")
            return
        keyboard = [[InlineKeyboardButton(s.strftime("%H:%M"), callback_data=f"time|{s.isoformat()}")] for s, _ in free_slots]
        keyboard.append([InlineKeyboardButton("Înapoi", callback_data=f"svc|{svc}")])
        await query.edit_message_text("Alege ora:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["step"] = "time"

    elif data.startswith("time|") and step == "time":
        start_dt = datetime.fromisoformat(data.split("|")[1]).replace(tzinfo=TZ)
        svc = context.user_data["service"]
        duration = SERVICES[svc][0]
        end_dt = start_dt + timedelta(minutes=duration)
        chat = update.effective_user
        name = context.user_data["name"]
        phone = context.user_data["phone"]
        price = context.user_data["price"]

        add_appointment(chat.id, name, phone, svc, price, start_dt, end_dt)

        # Notificare admin
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(f"📢 Programare nouă!\n👤 {name}\n📞 {phone}\n✂️ {svc} – {price} lei\n📅 {start_dt.date()}\n⏰ {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}")
            )

        await query.edit_message_text(f"✅ Programare confirmată!\n\n👤 {name}\n📞 {phone}\n✂️ {svc} – {price} lei\n📅 {start_dt.date()}\n⏰ {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}\n\nMulțumim!")

        context.user_data.clear()

async def text_response_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")
    if step == "name":
        context.user_data["name"] = update.message.text
        await ask_phone(update, context)
    elif step == "phone":
        context.user_data["phone"] = update.message.text
        await ask_service(update, context)

async def my_programari_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    rows = get_appointments_on(datetime.now(TZ).date())
    text_lines = ["Programările tale:"]
    for r in rows:
        if r[1] == chat_id:
            start_dt = datetime.fromisoformat(r[6]).replace(tzinfo=TZ)
            end_dt = datetime.fromisoformat(r[7]).replace(tzinfo=TZ)
            text_lines.append(f"{r[4]} – {start_dt.date()} {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}")
    await update.message.reply_text("\n".join(text_lines) if len(text_lines) > 1 else "Nu ai programări.")

# --- Main ---
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_response_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    print("Botul a pornit!")
    app.run_polling()

if __name__ == "__main__":
    main()

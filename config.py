# config.py

BOT_TOKEN = "8400099982:AAGnWVmQym6J_FXfXmW1_awkPKGVsMiaaYs"
ADMIN_BOT_TOKEN = "8487090698:AAEaS31sVgwL5GoaZhzIxPqiwPcEog04csw"
ADMIN_CHAT_ID = 6281972942  # Chat-ul unde adminul primește notificări

TIMEZONE = "Europe/Bucharest"

CONFIG = {
    "weekday_start": "19:30",
    "weekday_end": "22:00",
    "weekend_start": "10:00",
    "weekend_end": "17:00",
    "slot_minutes": 30,
    "days_ahead": 14
}

SERVICES = {
    "Tuns": {"duration": 30, "price": 40},
    "Tuns + Barbă": {"duration": 45, "price": 70},
    "Barbă": {"duration": 20, "price": 30}
}

DB_PATH = "appointments.db"

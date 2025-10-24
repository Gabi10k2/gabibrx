from flask import Flask, jsonify, render_template, request
import sqlite3
from config import DB_PATH, ADMIN_CHAT_ID

app = Flask(__name__)

def get_db_appointments():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, client_name, client_phone, service, price, start_ts, end_ts FROM appointments ORDER BY start_ts")
    rows = cur.fetchall()
    conn.close()
    appointments = []
    for r in rows:
        appointments.append({
            "id": r[0],
            "name": r[1],
            "phone": r[2],
            "service": r[3],
            "price": r[4],
            "start": r[5],
            "end": r[6]
        })
    return appointments

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/appointments")
def appointments():
    return jsonify(get_db_appointments())

@app.route("/delete/<int:app_id>", methods=["POST"])
def delete_appointment(app_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM appointments WHERE id=?", (app_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

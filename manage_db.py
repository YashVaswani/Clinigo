import sys
import os
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv()
import appointments_db
import config

def list_appointments(include_cancelled=False):
    with appointments_db.get_db_cursor() as cur:
        if include_cancelled:
            cur.execute("SELECT id, patient_name, patient_phone, appointment_date, appointment_time, status FROM appointments ORDER BY appointment_date, appointment_time")
        else:
            cur.execute("SELECT id, patient_name, patient_phone, appointment_date, appointment_time, status FROM appointments WHERE status = 'BOOKED' ORDER BY appointment_date, appointment_time")
        rows = cur.fetchall()

    print("\n" + "="*85)
    print(f"{'ID':<5} | {'Patient Name':<20} | {'Phone':<15} | {'Date':<12} | {'Time':<8} | {'Status':<10}")
    print("="*85)
    if not rows:
        print("  No appointments found.")
    for r in rows:
        aid, name, phone, dt, tm, status = r
        time_str = tm.strftime("%I:%M %p") if hasattr(tm, "strftime") else str(tm)
        print(f"{aid:<5} | {name:<20} | {phone:<15} | {str(dt):<12} | {time_str:<8} | {status:<10}")
    print("="*85 + "\n")

def cancel_by_id(appt_id):
    with appointments_db.get_db_cursor() as cur:
        cur.execute("UPDATE appointments SET status = 'CANCELLED' WHERE id = %s RETURNING patient_name, appointment_date, appointment_time", (appt_id,))
        row = cur.fetchone()
        if row:
            print(f"✅ Successfully cancelled appointment ID {appt_id} for {row[0]} on {row[1]} at {row[2]}")
        else:
            print(f"❌ No appointment found with ID {appt_id}")

def clear_all():
    confirm = input("⚠️  Are you sure you want to delete ALL appointments? (y/N): ")
    if confirm.lower() == 'y':
        with appointments_db.get_db_cursor() as cur:
            cur.execute("DELETE FROM appointments")
        print("🧹 All appointment records deleted.")

def show_slots(target_date=None):
    d = target_date or (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    slots = appointments_db.get_available_slots(d)
    print(f"\n📅 Available slots for {d}:")
    print("-" * 40)
    for s in slots:
        print(f"  • {s}")
    print(f"Total: {len(slots)} slots\n")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    
    if cmd == "list":
        list_appointments(include_cancelled=False)
    elif cmd == "all":
        list_appointments(include_cancelled=True)
    elif cmd == "cancel":
        if len(sys.argv) < 3:
            print("Usage: python manage_db.py cancel <appointment_id>")
        else:
            cancel_by_id(int(sys.argv[2]))
    elif cmd == "clear":
        clear_all()
    elif cmd == "slots":
        d = sys.argv[2] if len(sys.argv) > 2 else None
        show_slots(d)
    else:
        print("Commands: list, all, cancel <id>, clear, slots [YYYY-MM-DD]")

import os
import logging
from datetime import datetime, date, timedelta
import config

# Set environment variables for testing local database or Supabase/Neon
# Make sure DATABASE_URL is set before importing appointments_db
if not os.environ.get("DATABASE_URL"):
    print("Warning: DATABASE_URL is not set in environment. Trying to load from .env...")

import appointments_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_db")

def run_tests():
    print("=== STARTING DATABASE VERIFICATION ===")
    
    # 1. Initialize schema
    print("\n1. Initializing schema...")
    appointments_db.initialize_db()
    print("Schema initialized successfully!")
    
    # Let's find a future Monday date to avoid past-date validations.
    # Today is 2026-08-18 (Tuesday) according to prompt metadata.
    # Future Monday: 2026-08-24
    test_date_str = "2026-08-24"
    test_time_str = "09:30"
    test_name = "John Doe"
    test_phone = "+15550199"
    
    # 2. Get available slots
    print(f"\n2. Getting available slots for {test_date_str}...")
    slots = appointments_db.get_available_slots(test_date_str)
    print(f"Total slots generated: {len(slots)}")
    print(f"Sample slots: {slots[:5]}")
    assert test_time_str in slots, f"Expected {test_time_str} to be available"
    
    # 3. Clean up any existing active appointment for test phone to ensure test starting state
    print("\n3. Cleaning up old appointments...")
    with appointments_db.get_db_cursor() as cur:
        cur.execute(
            "UPDATE appointments SET status = 'CANCELLED' WHERE patient_phone = %s AND status = 'BOOKED'",
            (test_phone,)
        )
        cur.execute(
            "UPDATE appointments SET status = 'CANCELLED' WHERE appointment_date = %s AND appointment_time = %s AND status = 'BOOKED'",
            (datetime.strptime(test_date_str, "%Y-%m-%d").date(), datetime.strptime(test_time_str, "%H:%M").time())
        )
    
    # 4. Book appointment
    print(f"\n4. Booking appointment for {test_name} at {test_time_str} on {test_date_str}...")
    res = appointments_db.book_appointment(test_name, test_phone, test_date_str, test_time_str)
    print(f"Booking result: {res}")
    assert res["success"] is True, "Booking failed!"
    
    # 5. Verify slot is no longer available
    print("\n5. Checking slot availability after booking...")
    slots_after = appointments_db.get_available_slots(test_date_str)
    assert test_time_str not in slots_after, f"Expected slot {test_time_str} to be booked (not available)"
    print("Slot successfully removed from available list!")
    
    # 6. Verify double-booking prevention (Partial Unique Constraint)
    print("\n6. Testing double-booking constraint...")
    res_double = appointments_db.book_appointment("Jane Smith", "+15559999", test_date_str, test_time_str)
    print(f"Double-booking result: {res_double}")
    assert res_double["success"] is False, "Double-booking succeeded! Database constraint failed."
    assert "just booked" in res_double["message"] or "already booked" in res_double["message"], "Unexpected error message"
    print("Double-booking correctly prevented by PostgreSQL unique constraint!")
    
    # 7. Test rescheduling
    new_time_str = "10:00"
    print(f"\n7. Rescheduling appointment to {new_time_str}...")
    res_resched = appointments_db.reschedule_appointment(
        test_phone, test_date_str, test_time_str, test_date_str, new_time_str
    )
    print(f"Rescheduling result: {res_resched}")
    assert res_resched["success"] is True, "Rescheduling failed"
    
    # Verify old slot is free, new slot is taken
    slots_resched = appointments_db.get_available_slots(test_date_str)
    assert test_time_str in slots_resched, "Old slot should be free after reschedule"
    assert new_time_str not in slots_resched, "New slot should be taken after reschedule"
    print("Rescheduling verified successfully!")
    
    # 8. Test cancellation
    print(f"\n8. Cancelling appointment at {new_time_str}...")
    res_cancel = appointments_db.cancel_appointment(test_phone, test_date_str, new_time_str)
    print(f"Cancellation result: {res_cancel}")
    assert res_cancel["success"] is True, "Cancellation failed"
    
    # Verify new slot is free again
    slots_cancel = appointments_db.get_available_slots(test_date_str)
    assert new_time_str in slots_cancel, "Slot should be free after cancellation"
    print("Cancellation verified successfully!")
    
    print("\n=== ALL DATABASE TESTS PASSED SUCCESSFULLY ===")

if __name__ == "__main__":
    run_tests()

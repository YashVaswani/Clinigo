import os
import logging
import contextlib
from datetime import datetime, date, time, timedelta
import psycopg2
from psycopg2 import pool
import config

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("appointments_db")

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        db_url = config.DATABASE_URL
        if not db_url:
            logger.error("DATABASE_URL is not set!")
            raise ValueError("DATABASE_URL environment variable is missing. Set it in the .env file.")
        try:
            # Create a ThreadedConnectionPool with TCP keepalives to prevent cloud timeouts
            _pool = pool.ThreadedConnectionPool(
                1, 10,
                dsn=db_url,
                connect_timeout=4,
                keepalives=1,
                keepalives_idle=20,
                keepalives_interval=5,
                keepalives_count=3
            )
            logger.info("PostgreSQL connection pool initialized successfully.")
        except Exception as e:
            logger.exception("Failed to initialize PostgreSQL connection pool")
            raise
    return _pool

@contextlib.contextmanager
def get_db_cursor():
    p = get_pool()
    conn = None
    try:
        conn = p.getconn()
        # Test if connection is alive; recover gracefully if stale
        try:
            conn.cursor().execute("SELECT 1")
        except Exception:
            logger.warning("Stale database connection detected. Getting fresh connection...")
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
            conn = p.getconn()
    except Exception as e:
        if conn:
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
        logger.error(f"Failed to acquire DB connection: {e}")
        raise

    try:
        with conn.cursor() as cur:
            yield cur
            conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("Database transaction error, rolling back changes")
        raise e
    finally:
        if conn:
            try:
                p.putconn(conn)
            except Exception:
                pass


def initialize_db() -> None:
    """Creates the appointments table, indexes, and partial unique constraint to prevent double bookings."""
    queries = [
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            patient_name VARCHAR(100) NOT NULL,
            patient_phone VARCHAR(20) NOT NULL,
            appointment_date DATE NOT NULL,
            appointment_time TIME NOT NULL,
            status VARCHAR(20) DEFAULT 'BOOKED' CHECK (status IN ('BOOKED', 'CANCELLED'))
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments (appointment_date);",
        "CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments (patient_phone);",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS unique_active_appointment
        ON appointments (appointment_date, appointment_time)
        WHERE (status = 'BOOKED');
        """,
        # ---- Call Sessions table: records every voice call made to the agent ----
        """
        CREATE TABLE IF NOT EXISTS call_sessions (
            id SERIAL PRIMARY KEY,
            session_id VARCHAR(100) UNIQUE NOT NULL,
            caller_phone VARCHAR(20),
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMPTZ,
            duration_seconds INTEGER,
            transcript TEXT,
            summary TEXT,
            outcome VARCHAR(50)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_call_sessions_phone ON call_sessions (caller_phone);",
        "CREATE INDEX IF NOT EXISTS idx_call_sessions_started ON call_sessions (started_at DESC);"
    ]
    
    try:
        with get_db_cursor() as cur:
            for query in queries:
                cur.execute(query)
        logger.info("Database schema and indexes initialized/verified successfully.")
    except Exception as e:
        logger.critical(f"Failed to run database migrations: {e}")
        raise


def log_call_session(
    session_id: str,
    caller_phone: str | None,
    started_at,
    ended_at,
    transcript: str,
    summary: str = "",
    outcome: str = "",
) -> None:
    """
    Saves the full record of a completed call session to the database.
    
    Args:
        session_id: LiveKit room name or unique session identifier
        caller_phone: Caller's phone number (from SIP) or None for web sessions
        started_at: datetime when the session started
        ended_at: datetime when the session ended
        transcript: Full text transcript of the conversation
        summary: Brief one-sentence summary of what was accomplished
        outcome: e.g. 'BOOKED', 'CANCELLED', 'RESCHEDULED', 'INQUIRY', 'NO_ACTION'
    """
    duration = int((ended_at - started_at).total_seconds()) if ended_at and started_at else None
    try:
        with get_db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO call_sessions 
                    (session_id, caller_phone, started_at, ended_at, duration_seconds, transcript, summary, outcome)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    ended_at = EXCLUDED.ended_at,
                    duration_seconds = EXCLUDED.duration_seconds,
                    transcript = EXCLUDED.transcript,
                    summary = EXCLUDED.summary,
                    outcome = EXCLUDED.outcome
                """,
                (session_id, caller_phone, started_at, ended_at, duration, transcript, summary, outcome)
            )
        logger.info(f"Call session saved: {session_id} | Duration: {duration}s | Outcome: {outcome}")
    except Exception as e:
        logger.exception(f"Failed to save call session {session_id}")


def get_call_sessions(limit: int = 50) -> list[dict]:
    """Returns the most recent call sessions for review with full conversation transcripts."""
    try:
        with get_db_cursor() as cur:
            cur.execute(
                """
                SELECT session_id, caller_phone, started_at, ended_at,
                       duration_seconds, transcript, summary, outcome
                FROM call_sessions
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,)
            )
            rows = cur.fetchall()
        return [
            {
                "session_id": r[0], "caller_phone": r[1],
                "started_at": str(r[2]), "ended_at": str(r[3]),
                "duration_seconds": r[4], "transcript": r[5] or "",
                "summary": r[6] or "", "outcome": r[7] or "COMPLETED"
            }
            for r in rows
        ]
    except Exception as e:
        logger.exception("Failed to retrieve call sessions")
        return []


def get_available_slots(date_str: str) -> list[str]:
    """Generates and returns available 30-minute appointment slots for a given date in YYYY-MM-DD format."""
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"Invalid date format received: {date_str}")
        return []

    # Check if clinic is open on this weekday
    weekday = parsed_date.weekday()
    if weekday not in config.CLINIC_HOURS:
        logger.info(f"Clinic is closed on {date_str} (weekday={weekday})")
        return []

    # Clinic hours configuration
    start_str, end_str = config.CLINIC_HOURS[weekday]
    try:
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()
        
        lunch_start = datetime.strptime(config.LUNCH_BREAK[0], "%H:%M").time()
        lunch_end = datetime.strptime(config.LUNCH_BREAK[1], "%H:%M").time()
    except ValueError:
        logger.exception("Error parsing clinic hours or lunch break from config")
        return []

    # Generate all candidate slots
    slots = []
    current_dt = datetime.combine(date.min, start_time)
    end_dt = datetime.combine(date.min, end_time)
    slot_delta = timedelta(minutes=config.SLOT_DURATION_MINS)

    while current_dt + slot_delta <= end_dt:
        slot_time = current_dt.time()
        # Skip if slot overlaps/starts during lunch break
        if not (lunch_start <= slot_time < lunch_end):
            slots.append(slot_time.strftime("%H:%M"))
        current_dt += slot_delta

    # Fetch booked slots from DB
    booked_slots = []
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT appointment_time FROM appointments WHERE appointment_date = %s AND status = 'BOOKED'",
                (parsed_date,)
            )
            booked_slots = [row[0].strftime("%H:%M") for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch booked slots: {e}")
        return []

    # Filter out already booked slots
    available = [s for s in slots if s not in booked_slots]

    # If querying for today, exclude slots in the past
    if parsed_date == date.today():
        now_str = datetime.now().strftime("%H:%M")
        available = [s for s in available if s > now_str]

    return available

import re

def normalize_indian_phone(phone_str: str) -> tuple[bool, str]:
    """Validates and normalizes phone numbers to standard 10-digit Indian mobile numbers."""
    if not phone_str:
        return False, ""
    digits = re.sub(r'\D', '', str(phone_str))
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    
    if len(digits) == 10:
        return True, digits
    return False, digits

def book_appointment(name: str, phone: str, date_str: str, time_str: str) -> dict:
    """Atomically books an appointment. Returns a status dictionary."""
    is_valid, clean_phone = normalize_indian_phone(phone)
    if not is_valid:
        return {"success": False, "message": f"'{phone}' is not a valid 10-digit mobile number. Please provide a valid 10-digit Indian phone number."}
    phone = clean_phone

    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        parsed_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return {"success": False, "message": "Invalid date or time format. Expected YYYY-MM-DD and HH:MM."}

    # Time validation: block past bookings
    if parsed_date < date.today():
        return {"success": False, "message": "Cannot book appointments in the past."}
    if parsed_date == date.today() and parsed_time <= datetime.now().time():
        return {"success": False, "message": "Cannot book slots in the past."}

    weekday = parsed_date.weekday()
    if weekday not in config.CLINIC_HOURS:
        return {"success": False, "message": "The clinic is closed on this day."}

    start_str, end_str = config.CLINIC_HOURS[weekday]
    start_time = datetime.strptime(start_str, "%H:%M").time()
    end_time = datetime.strptime(end_str, "%H:%M").time()
    if not (start_time <= parsed_time < end_time):
        return {"success": False, "message": f"Time slot is outside working hours ({start_str} - {end_str})."}

    lunch_start = datetime.strptime(config.LUNCH_BREAK[0], "%H:%M").time()
    lunch_end = datetime.strptime(config.LUNCH_BREAK[1], "%H:%M").time()
    if lunch_start <= parsed_time < lunch_end:
        return {"success": False, "message": "This slot falls during the lunch break."}

    try:
        with get_db_cursor() as cur:
            # Check if this caller already has an active booking on this date (prevent multiples)
            cur.execute(
                "SELECT id FROM appointments WHERE patient_phone = %s AND appointment_date = %s AND status = 'BOOKED'",
                (phone, parsed_date)
            )
            if cur.fetchone():
                return {"success": False, "message": "You already have an appointment booked on this date."}

            # Attempt insertion (violating unique constraint will raise UniqueViolation)
            cur.execute(
                """
                INSERT INTO appointments (patient_name, patient_phone, appointment_date, appointment_time, status)
                VALUES (%s, %s, %s, %s, 'BOOKED')
                RETURNING id
                """,
                (name, phone, parsed_date, parsed_time)
            )
            appointment_id = cur.fetchone()[0]

        logger.info(f"Booked appt: ID={appointment_id} for {name} ({phone}) on {date_str} at {time_str}")
        return {
            "success": True,
            "message": f"Appointment booked successfully for {name} on {date_str} at {time_str}.",
            "appointment_id": appointment_id
        }
    except psycopg2.errors.UniqueViolation:
        logger.warning(f"Double-booking attempt prevented for {date_str} at {time_str}")
        return {"success": False, "message": "This slot was just booked by another patient. Please select a different time."}
    except Exception as e:
        logger.exception("Error booking appointment")
        return {"success": False, "message": "An internal database error occurred. Please try again."}

def get_appointment_details(search_query: str) -> dict:
    """
    Searches for active appointments (status='BOOKED') using patient name or phone number.
    
    Args:
        search_query: Patient's name or phone number
    """
    try:
        search_pattern = f"%{search_query.strip().lower()}%"
        with get_db_cursor() as cur:
            cur.execute(
                """
                SELECT patient_name, patient_phone, appointment_date, appointment_time
                FROM appointments
                WHERE (LOWER(patient_name) LIKE %s OR patient_phone LIKE %s)
                  AND status = 'BOOKED'
                ORDER BY appointment_date ASC, appointment_time ASC
                """,
                (search_pattern, search_pattern)
            )
            rows = cur.fetchall()
            if not rows:
                return {"success": False, "message": f"No active appointments found for '{search_query}'."}
            
            appts_list = []
            for r in rows:
                date_str = r[2].strftime("%Y-%m-%d")
                time_str = r[3].strftime("%H:%M")
                appts_list.append(f"Patient Name: {r[0]}, Phone: {r[1]}, Date: {date_str}, Time: {time_str}")
            
            message = "I found the following active appointment(s):\n" + "\n".join(appts_list)
            return {"success": True, "message": message}
    except Exception as e:
        logger.exception("Error searching appointments")
        return {"success": False, "message": "An internal database error occurred while searching."}

def cancel_appointment(patient_identifier: str = None, date_str: str = None, time_str: str = None, name: str = None, phone: str = None) -> dict:
    """Cancels an existing appointment matching the patient name or phone, date, and optionally time."""
    identifier = (patient_identifier or phone or name or "").strip()
    if not identifier:
        return {"success": False, "message": "Patient name or phone number is required to cancel an appointment."}

    if not date_str:
        return {"success": False, "message": "Appointment date is required."}

    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"success": False, "message": "Invalid date format. Please use YYYY-MM-DD."}

    parsed_time = None
    if time_str:
        try:
            parsed_time = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            parsed_time = None

    try:
        with get_db_cursor() as cur:
            if parsed_time:
                cur.execute(
                    """
                    UPDATE appointments
                    SET status = 'CANCELLED'
                    WHERE (LOWER(patient_name) = LOWER(%s) OR patient_phone = %s)
                      AND appointment_date = %s AND appointment_time = %s AND status = 'BOOKED'
                    RETURNING id, patient_name, patient_phone, appointment_time
                    """,
                    (identifier, identifier, parsed_date, parsed_time)
                )
            else:
                cur.execute(
                    """
                    UPDATE appointments
                    SET status = 'CANCELLED'
                    WHERE (LOWER(patient_name) = LOWER(%s) OR patient_phone = %s)
                      AND appointment_date = %s AND status = 'BOOKED'
                    RETURNING id, patient_name, patient_phone, appointment_time
                    """,
                    (identifier, identifier, parsed_date)
                )
            row = cur.fetchone()
            if not row:
                return {"success": False, "message": f"No active appointment found for '{identifier}' on {date_str}."}

        logger.info(f"Cancelled appt: Name/Phone={identifier}, Date={date_str}, Time={row[3]}")
        return {"success": True, "message": f"Appointment for {row[1]} ({row[2]}) on {date_str} at {row[3]} has been cancelled successfully."}
    except Exception as e:
        logger.exception("Error cancelling appointment")
        return {"success": False, "message": "An internal database error occurred."}

def reschedule_appointment(patient_identifier: str = None, old_date_str: str = None, old_time_str: str = None, new_date_str: str = None, new_time_str: str = None, name: str = None, phone: str = None) -> dict:
    """Atomically reschedules an appointment by cancelling the old one and booking a new one."""
    identifier = (patient_identifier or phone or name or "").strip()
    if not identifier:
        return {"success": False, "message": "Patient name or phone number is required to reschedule."}
    try:
        old_date = datetime.strptime(old_date_str, "%Y-%m-%d").date()
        old_time = datetime.strptime(old_time_str, "%H:%M").time()
        new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
        new_time = datetime.strptime(new_time_str, "%H:%M").time()
    except ValueError:
        return {"success": False, "message": "Invalid date or time format."}

    # Time validation: block past bookings
    if new_date < date.today():
        return {"success": False, "message": "Cannot reschedule to a past date."}
    if new_date == date.today() and new_time <= datetime.now().time():
        return {"success": False, "message": "Cannot reschedule to a past time slot."}

    weekday = new_date.weekday()
    if weekday not in config.CLINIC_HOURS:
        return {"success": False, "message": "The clinic is closed on the new date."}

    start_str, end_str = config.CLINIC_HOURS[weekday]
    start_time = datetime.strptime(start_str, "%H:%M").time()
    end_time = datetime.strptime(end_str, "%H:%M").time()
    if not (start_time <= new_time < end_time):
        return {"success": False, "message": f"New slot is outside working hours ({start_str} - {end_str})."}

    lunch_start = datetime.strptime(config.LUNCH_BREAK[0], "%H:%M").time()
    lunch_end = datetime.strptime(config.LUNCH_BREAK[1], "%H:%M").time()
    if lunch_start <= new_time < lunch_end:
        return {"success": False, "message": "New slot falls during the lunch break."}

    try:
        with get_db_cursor() as cur:
            # 1. Fetch original booking details using name/phone search
            cur.execute(
                """
                SELECT id, patient_name, patient_phone 
                FROM appointments 
                WHERE (LOWER(patient_name) = LOWER(%s) OR patient_phone = %s OR LOWER(patient_name) = LOWER(%s) OR patient_phone = %s)
                  AND appointment_date = %s AND appointment_time = %s AND status = 'BOOKED'
                """,
                (identifier, identifier, name or identifier, phone or identifier, old_date, old_time)
            )
            row = cur.fetchone()
            if not row:
                return {"success": False, "message": "No original active appointment found to reschedule."}
            
            appt_id, name, phone = row
            
            # 2. Cancel original booking
            cur.execute(
                "UPDATE appointments SET status = 'CANCELLED' WHERE id = %s",
                (appt_id,)
            )
            
            # 3. Create new booking
            cur.execute(
                """
                INSERT INTO appointments (patient_name, patient_phone, appointment_date, appointment_time, status)
                VALUES (%s, %s, %s, %s, 'BOOKED')
                RETURNING id
                """,
                (name, phone, new_date, new_time)
            )
            new_id = cur.fetchone()[0]

        logger.info(f"Rescheduled appt: Name/Phone={identifier} from {old_date_str} {old_time_str} to {new_date_str} {new_time_str} (New ID={new_id})")
        return {"success": True, "message": f"Appointment successfully rescheduled to {new_date_str} at {new_time_str}."}

    except psycopg2.errors.UniqueViolation:
        logger.warning(f"Reschedule double-booking prevented for slot {new_date_str} {new_time_str}")
        return {"success": False, "message": "The requested new slot is already booked. Rescheduling failed."}
    except Exception as e:
        logger.exception("Error rescheduling appointment")
        return {"success": False, "message": "An internal database error occurred."}

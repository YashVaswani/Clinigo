import os
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# LiveKit connection
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# API Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "c6bbc7d5-4b35-4d49-b1c6-4417019a61c1")

# PostgreSQL Database URL (Supabase or Neon)
DATABASE_URL = os.getenv("DATABASE_URL")

# Local LLM Fallback (optional)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")

# Clinic Settings
CLINIC_NAME = "Healthcare Clinic"
DOCTOR_NAME = "Dr. Umang Chourasia"
SLOT_DURATION_MINS = 30

# Working Days and Hours
# 0 = Monday, 1 = Tuesday, 2 = Wednesday, 3 = Thursday, 4 = Friday, 5 = Saturday, 6 = Sunday
CLINIC_HOURS = {
    0: ("09:00", "17:00"),  # Monday
    1: ("09:00", "17:00"),  # Tuesday
    2: ("09:00", "17:00"),  # Wednesday
    3: ("09:00", "17:00"),  # Thursday
    4: ("09:00", "17:00"),  # Friday
    5: ("09:00", "17:00"),  # Saturday
}

# Lunch Break
LUNCH_BREAK = ("12:00", "13:00")

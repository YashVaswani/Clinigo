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
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_RIVA_SERVER = os.getenv("NVIDIA_RIVA_SERVER", "grpc.nvcf.nvidia.com:443")
FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY")
FISH_AUDIO_VOICE_ID_HINDI = os.getenv("FISH_AUDIO_VOICE_ID_HINDI")
FISH_AUDIO_VOICE_ID_ENGLISH = os.getenv("FISH_AUDIO_VOICE_ID_ENGLISH")

# Cartesia TTS
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID")


# PostgreSQL Database URL (Supabase or Neon)
DATABASE_URL = os.getenv("DATABASE_URL")

# Local model endpoints for STT, TTS, and LLM
WHISPER_URL = os.getenv("WHISPER_URL", "http://localhost:8000/v1")
KOKORO_URL = os.getenv("KOKORO_URL", "http://localhost:8880/v1")
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

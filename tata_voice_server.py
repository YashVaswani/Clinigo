import asyncio
import json
import base64
import logging
import os
from datetime import datetime, date, timedelta
import aiohttp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

import config
import appointments_db

# Try to import audioop (or fallback to audioop-lts on Python 3.14+)
try:
    import audioop
except ImportError:
    import audioop_lts as audioop

# Import LiveKit SDK modules
from livekit import rtc
from livekit.agents import llm, tts, APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.plugins import silero, cartesia, openai

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Setup Logging with explicit flush to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
logger = logging.getLogger("tata_voice_server")


# ----- Helper to detect Hindi characters -----
def _detect_hindi(text: str) -> bool:
    for char in text:
        if '\u0900' <= char <= '\u097f':
            return True
    return False


def _normalize_text_for_speech(text: str) -> str:
    """Normalizes phone numbers, times, currencies, and strips markdown for crystal clear speech."""
    import re

    # 1. Strip markdown bold, bullets, asterisks, hashtags
    text = re.sub(r'[*#_`]', '', text)
    text = re.sub(r'^\s*[-•]\s*', '', text, flags=re.MULTILINE)

    # 2. Convert 24-hr times (e.g. 13:00 -> 1:00 PM, 09:30 -> 9:30 AM)
    def format_time(m):
        hh, mm = int(m.group(1)), m.group(2)
        period = 'AM' if hh < 12 else 'PM'
        display_h = hh if 1 <= hh <= 12 else (hh - 12 if hh > 12 else 12)
        return f'{display_h}:{mm} {period}'
    text = re.sub(r'\b([01]?\d|2[0-3]):([0-5]\d)(?!\s*(?:AM|PM|am|pm))\b', format_time, text)

    # Clean space times for English TTS natural flow (e.g. 11:30 AM -> 11 30 AM, 1:00 PM -> 1 PM)
    text = re.sub(r'(\d{1,2}):00\s*(AM|PM|am|pm)', r'\1 \2', text)
    text = re.sub(r'(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)', r'\1 \2 \3', text)

    # 3. Convert ₹ / Rs / Rs. to spoken words
    is_hindi = _detect_hindi(text)
    curr_word = "रुपये" if is_hindi else "rupees"
    text = re.sub(r'\b[Rr][Ss]\.?\s*(\d+)', rf'\1 {curr_word}', text)
    text = re.sub(r'[₹]\s*(\d+)', rf'\1 {curr_word}', text)
    text = re.sub(r'\b[Rr][Ss]\.?\b', curr_word, text)
    text = re.sub(r'[₹]', f" {curr_word} ", text)

    # 4. Format phone numbers (7 to 13 digits, with optional hyphens/spaces/+91) into comma-separated individual digits for crystal-clear recitation
    def format_phone(m):
        raw_digits = re.sub(r'\D', '', m.group(0))
        if len(raw_digits) >= 10:
            d = raw_digits[-10:]
            # Group into 5 and 5 with commas for natural cadence: '9, 8, 7, 6, 5,  4, 3, 2, 1, 0'
            return ', '.join(list(d[:5])) + ',  ' + ', '.join(list(d[5:]))
        return ', '.join(list(raw_digits))
    phone_pattern = r'(?:\+?91[\s\-‑.]*)?(?:\b\d{3}[\s\-‑.]*\d{3}[\s\-‑.]*\d{4}\b|\b\d{5}[\s\-‑.]*\d{5}\b|\b\d{7,13}\b)'
    text = re.sub(phone_pattern, format_phone, text)

    # 5. Phonetic pronunciation enhancements for Cartesia TTS
    text = re.sub(r'\bGunjan\b', 'Gunjan', text)

    return text.strip()


# ----- WAV Audio Header Encoder for Gemini Multimodal ASR -----
def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> bytes:
    import struct
    channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    data_size = len(pcm_bytes)
    chunk_size = 36 + data_size
    
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', chunk_size, b'WAVE', b'fmt ', 16, 1,
        channels, sample_rate, byte_rate, block_align, bits_per_sample,
        b'data', data_size
    )
    return header + pcm_bytes


GLOBAL_HTTP_SESSION = None
GREETING_PCM_8K = None


async def get_http_session() -> aiohttp.ClientSession:
    global GLOBAL_HTTP_SESSION
    if GLOBAL_HTTP_SESSION is None or GLOBAL_HTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(limit=50, keepalive_timeout=60, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=10)
        GLOBAL_HTTP_SESSION = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return GLOBAL_HTTP_SESSION


def _clean_audio_tail(raw_pcm: bytes, fade_ms: int = 40, sample_rate: int = 8000) -> bytes:
    """Applies a smooth fade-out to the last few milliseconds of audio to eliminate clicks, pops, and tail murmurs."""
    import struct
    fade_samples = int(sample_rate * (fade_ms / 1000.0))
    if len(raw_pcm) < fade_samples * 2:
        return raw_pcm

    total_samples = len(raw_pcm) // 2
    samples = list(struct.unpack(f"<{total_samples}h", raw_pcm))
    for i in range(fade_samples):
        idx = total_samples - fade_samples + i
        factor = (fade_samples - i) / float(fade_samples)
        samples[idx] = int(samples[idx] * factor)

    return struct.pack(f"<{total_samples}h", *samples)


async def precompute_greeting():
    """Pre-renders the initial greeting into 8kHz PCM so calls start with 0ms delay."""
    global GREETING_PCM_8K
    if not config.CARTESIA_API_KEY:
        return
    try:
        session = await get_http_session()
        greeting_text = f"Hello! Welcome to {config.CLINIC_NAME}. I am Gunjan, how may I help you today?"
        cartesia_url = "https://api.cartesia.ai/tts/bytes"
        c_headers = {
            "X-API-Key": config.CARTESIA_API_KEY,
            "Cartesia-Version": "2024-06-10",
            "Content-Type": "application/json"
        }
        c_payload = {
            "model_id": "sonic-2",
            "transcript": greeting_text,
            "language": "en",
            "voice": {
                "mode": "id",
                "id": config.CARTESIA_VOICE_ID or "c6bbc7d5-4b35-4d49-b1c6-4417019a61c1"
            },
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 8000
            }
        }
        async with session.post(cartesia_url, headers=c_headers, json=c_payload) as resp:
            if resp.status == 200:
                raw_8k = await resp.read()
                GREETING_PCM_8K = _clean_audio_tail(raw_8k, fade_ms=40, sample_rate=8000)
                logger.info(f"Pre-rendered initial greeting audio in memory ({len(GREETING_PCM_8K)} bytes at 8kHz).")
            else:
                err_text = await resp.text()
                logger.warning(f"Failed to pre-render greeting. Cartesia status: {resp.status}, error: {err_text}")
    except Exception as e:
        logger.exception("Failed to pre-render greeting")


async def transcribe_audio_via_groq(pcm_16k: bytes, api_key: str = None) -> str:
    """Transcribes 16kHz PCM audio using Groq Whisper-large-v3-turbo (lightning fast + multi-lingual Hindi/English)."""
    key = api_key or config.GROQ_API_KEY
    if not key or not pcm_16k:
        return ""

    try:
        session = await get_http_session()
        wav_bytes = pcm_to_wav(pcm_16k, sample_rate=16000)

        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {key}"}

        data = aiohttp.FormData()
        data.add_field("file", wav_bytes, filename="speech.wav", content_type="audio/wav")
        data.add_field("model", "whisper-large-v3-turbo")
        data.add_field("prompt", "Doctor consultation, Hindi and English conversation.")
        data.add_field("temperature", "0.0")

        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                text = result.get("text", "").strip()
                if text:
                    logger.info(f"Groq Whisper ASR Recognized: {text}")
                return text
            else:
                err = await resp.text()
                logger.warning(f"Groq Whisper ASR failed ({resp.status}): {err}")
                return ""
    except Exception as e:
        logger.error(f"Error in Groq Whisper ASR: {e}")
        return ""


async def transcribe_audio_via_cartesia(pcm_16k: bytes) -> str:
    """Transcribes 16kHz PCM audio using Cartesia Ink-Whisper multilingual STT."""
    from livekit.plugins import cartesia
    from livekit.agents import utils

    if not config.CARTESIA_API_KEY or not pcm_16k:
        return ""

    try:
        async with utils.http_context.open():
            stt_instance = cartesia.STT(
                api_key=config.CARTESIA_API_KEY,
                model='ink-whisper',
                sample_rate=16000,
                encoding='pcm_s16le',
                language=None  # Multilingual auto-detect (Hindi, Hinglish, English)
            )
            stream = stt_instance.stream()
            final_text = []

            async def read_stream():
                try:
                    async for event in stream:
                        if event.alternatives and event.alternatives[0].text:
                            final_text.append(event.alternatives[0].text)
                except Exception:
                    pass

            reader_task = asyncio.create_task(read_stream())

            # Push audio in 40ms frames (1280 bytes at 16kHz mono)
            chunk_size = 1280
            for i in range(0, len(pcm_16k), chunk_size):
                chunk = pcm_16k[i:i+chunk_size]
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=16000,
                    num_channels=1,
                    samples_per_channel=len(chunk) // 2
                )
                stream.push_frame(frame)
                await asyncio.sleep(0.001)

            stream.end_input()
            try:
                await asyncio.wait_for(reader_task, timeout=2.5)
            except asyncio.TimeoutError:
                pass
            await stream.aclose()

            result_text = " ".join(dict.fromkeys(final_text)).strip()
            if result_text:
                logger.info(f"Cartesia Ink-Whisper ASR Recognized: {result_text}")
            return result_text
    except Exception as e:
        logger.error(f"Cartesia Ink-Whisper STT error: {e}")
        return ""


# ----- Dynamic Cartesia TTS Wrapper (Hindi/English Accent selection) -----
class DynamicCartesiaTTS(tts.TTS):
    def __init__(self, api_key: str, voice_id: str):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=24000,
            num_channels=1
        )
        self.api_key = api_key
        self.voice_id = voice_id
        self.tts_english = cartesia.TTS(api_key=api_key, voice=voice_id, language="en", model="sonic-2")
        self.tts_hindi = cartesia.TTS(api_key=api_key, voice=voice_id, language="hi", model="sonic-2")

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = None) -> tts.ChunkedStream:
        opts = conn_options or DEFAULT_API_CONNECT_OPTIONS
        if _detect_hindi(text):
            return self.tts_hindi.synthesize(text, conn_options=opts)
        else:
            return self.tts_english.synthesize(text, conn_options=opts)


# ----- Custom Gemini LLM Connector using aiohttp (bypasses httpx/anyio issues) -----
def _extract_msg_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and "text" in p:
                parts.append(p["text"])
        return " ".join(parts)
    return ""


# ----- Clinic Appointment Tools Definition for Groq -----
GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": "Check available 30-min appointment slots for Dr. Umang Chourasia on a specific date (YYYY-MM-DD)",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": "string", "description": "Date in YYYY-MM-DD format (e.g. 2026-08-26)"}
                },
                "required": ["target_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book a clinic appointment for a patient with Dr. Umang Chourasia",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string", "description": "Full name of the patient"},
                    "patient_phone": {"type": "string", "description": "10-digit phone number"},
                    "appointment_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "appointment_time": {"type": "string", "description": "Time in HH:MM format (e.g. 09:00, 14:30)"}
                },
                "required": ["patient_name", "patient_phone", "appointment_date", "appointment_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an existing appointment for a patient by their full name or phone number",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string", "description": "Full name of the patient"},
                    "patient_phone": {"type": "string", "description": "Phone number of the patient (optional if name is provided)"},
                    "appointment_date": {"type": "string", "description": "Date in YYYY-MM-DD format (optional)"},
                    "appointment_time": {"type": "string", "description": "Time in HH:MM format (optional)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Reschedule an existing appointment to a new date and time using patient name or phone",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string", "description": "Full name of the patient"},
                    "patient_phone": {"type": "string", "description": "Phone number of the patient (optional if name is provided)"},
                    "old_date": {"type": "string", "description": "Original appointment date in YYYY-MM-DD format"},
                    "old_time": {"type": "string", "description": "Original appointment time in HH:MM format (optional)"},
                    "new_date": {"type": "string", "description": "New appointment date in YYYY-MM-DD format"},
                    "new_time": {"type": "string", "description": "New appointment time in HH:MM format"}
                },
                "required": ["new_date", "new_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_appointment_details",
            "description": "Look up existing appointments for a patient by name or phone number",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string", "description": "Full name of the patient"},
                    "patient_phone": {"type": "string", "description": "Phone number of the patient"}
                }
            }
        }
    }
]


def _execute_clinic_tool(func_name: str, args: dict) -> dict:
    """Executes the requested appointment database tool and returns a result dict."""
    try:
        # Sanitize tool name if model appended channel/commentary tokens
        clean_name = func_name.split('<')[0].split('|')[0].strip()

        if clean_name == "get_available_slots":
            date_str = args.get("target_date")
            slots = appointments_db.get_available_slots(date_str)
            return {"target_date": date_str, "available_slots": slots, "count": len(slots)}
        elif clean_name == "book_appointment":
            phone = args.get("patient_phone")
            if not phone or "<missing>" in str(phone) or "unknown" in str(phone).lower():
                return {"success": False, "message": "Phone number is missing. Please ask the caller: 'And what 10-digit mobile number should we register for the appointment?'"}
            return appointments_db.book_appointment(
                name=args.get("patient_name"),
                phone=str(phone).strip(),
                date_str=args.get("appointment_date"),
                time_str=args.get("appointment_time")
            )
        elif clean_name == "cancel_appointment":
            return appointments_db.cancel_appointment(
                name=args.get("patient_name"),
                phone=args.get("patient_phone"),
                date_str=args.get("appointment_date"),
                time_str=args.get("appointment_time")
            )
        elif clean_name == "reschedule_appointment":
            return appointments_db.reschedule_appointment(
                name=args.get("patient_name"),
                phone=args.get("patient_phone"),
                old_date_str=args.get("old_date"),
                old_time_str=args.get("old_time"),
                new_date_str=args.get("new_date"),
                new_time_str=args.get("new_time")
            )
        elif clean_name == "get_appointment_details":
            query = args.get("patient_phone") or args.get("patient_name") or args.get("search_query") or ""
            return appointments_db.get_appointment_details(search_query=query)
    except Exception as e:
        logger.error(f"Error executing tool {func_name}: {e}")
        return {"error": str(e)}
    return {"error": f"Unknown tool {func_name}"}
    return {"error": f"Unknown tool: {func_name}"}


class LLMTextChunk:
    """Simple wrapper for a streamed text delta."""
    def __init__(self, text: str):
        self.text = text


def _extract_spoken_text_from_failed_generation(err_json_str: str) -> str:
    """Extracts actual spoken response text if Groq returned 400 with a failed_generation."""
    import re
    try:
        data = json.loads(err_json_str)
        failed_gen = data.get("error", {}).get("failed_generation", "")
        if failed_gen:
            m = re.search(r'["\']?arguments["\']?\s*:\s*"?([^}"]+)', failed_gen)
            if m:
                return m.group(1).strip(' "{\n\r\t')
    except Exception:
        pass
    return ""


class GroqToolLLMStream:
    def __init__(self, chat_ctx: llm.ChatContext, api_key: str, model: str):
        self._chat_ctx = chat_ctx
        self._api_key = api_key
        self._model = model

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        # Convert chat context to OpenAI format
        messages = []
        for msg in self._chat_ctx.messages():
            role = msg.role
            text = _extract_msg_text(msg.content)
            if text.strip():
                api_role = "system" if role == "system" else ("user" if role == "user" else "assistant")
                messages.append({"role": api_role, "content": text})

        session = await get_http_session()
        # Keep system instructions + last 16 conversation turns to prevent mid-call memory loss
        system_msgs = [m for m in messages if m["role"] == "system"]
        chat_msgs = [m for m in messages if m["role"] != "system"][-16:]
        cur_messages = system_msgs + chat_msgs

        try:
            for step in range(4):
                payload = {
                    "model": self._model,
                    "messages": cur_messages,
                    "tools": GROQ_TOOLS,
                    "tool_choice": "none" if step > 0 else "auto",
                    "temperature": 0.2
                }

                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        logger.error(f"Groq API error {resp.status}: {err_text}")
                        # Auto-recover the spoken text from failed_generation if model wrapped text in commentary
                        recovered = _extract_spoken_text_from_failed_generation(err_text)
                        if recovered:
                            logger.info(f"Recovered response from failed_generation: {recovered}")
                            yield LLMTextChunk(recovered)
                            return

                        yield LLMTextChunk("I'd be happy to help you with your appointment. Which date and time would you like?")
                        return

                    data = await resp.json()
                    choice = data.get("choices", [{}])[0].get("message", {})
                    tool_calls = choice.get("tool_calls", [])

                    if tool_calls:
                        call = tool_calls[0]
                        fn_name = call["function"]["name"]
                        try:
                            fn_args = json.loads(call["function"]["arguments"])
                        except Exception:
                            fn_args = {}

                        logger.info(f"Groq LLM Tool Call: {fn_name}({fn_args})")
                        tool_result = _execute_clinic_tool(fn_name, fn_args)
                        logger.info(f"Groq LLM Tool Result: {tool_result}")

                        cur_messages.append(choice)
                        cur_messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": fn_name,
                            "content": json.dumps(tool_result)
                        })
                    else:
                        content = choice.get("content", "")
                        if content:
                            yield LLMTextChunk(content)
                        break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in GroqToolLLMStream: {e}", exc_info=True)


class GroqToolLLM(llm.LLM):
    def __init__(self, api_key: str, model: str = "openai/gpt-oss-120b"):
        super().__init__()
        self._api_key = api_key
        self._model = model

    def chat(self, *, chat_ctx: llm.ChatContext, conn_options=None) -> GroqToolLLMStream:
        return GroqToolLLMStream(chat_ctx=chat_ctx, api_key=self._api_key, model=self._model)


# ----- Global Provider Instances -----
stt_provider = None
tts_provider = None
llm_provider = None


def initialize_providers():
    global llm_provider

    # 2. TTS Provider Info
    logger.info(f"Initializing Cartesia TTS (sonic-turbo | Voice: {config.CARTESIA_VOICE_ID or 'c6bbc7d5-4b35-4d49-b1c6-4417019a61c1'})...")

    # 3. LLM Provider (Groq Tool LLM with sub-150ms LPU execution)
    logger.info("Initializing Groq Tool LLM (openai/gpt-oss-120b)...")
    llm_provider = GroqToolLLM(api_key=config.GROQ_API_KEY, model="openai/gpt-oss-120b")


# ----- Live Voice Call Session Manager -----
class VoiceSession:
    """Manages full bidirectional audio and turns for a single live phone call session."""

    def __init__(self, websocket, stream_sid: str, caller_phone: str | None):
        self.websocket = websocket
        self.stream_sid = stream_sid
        self.caller_phone = caller_phone
        self.chunk_counter = 1
        self.closed = False

        # Conversation state & turn synchronizer
        self.turn_lock = asyncio.Lock()
        self.active_turn_tasks = set()
        self.playout_task = None
        self.cancel_playback_flag = False
        self.playout_start_time = None  # Tracks when current speech started (for grace period)
        self.is_speaking = False        # True while TTS audio is playing — blocks echo from being fed to VAD

        # Booking state machine — tracks which step the booking is on
        # Prevents farewell from firing mid-booking on "thank you"
        self.booking_state = {
            "active": False,      # True once caller has started booking flow
            "date": None,
            "time": None,
            "name": None,
            "phone": None,
            "confirmed": False    # True only after book_appointment succeeds
        }
        # Format caller's incoming phone number if available
        self.clean_caller_phone = None
        if caller_phone:
            is_valid, clean_p = appointments_db.normalize_indian_phone(caller_phone)
            if is_valid:
                self.clean_caller_phone = clean_p
            else:
                self.clean_caller_phone = re.sub(r'\D', '', str(caller_phone))

        caller_num_text = f" (Caller ID: {self.clean_caller_phone})" if self.clean_caller_phone else ""
        caller_phone_instruction = ""
        if self.clean_caller_phone:
            caller_phone_instruction = f"""
- CALLER PHONE NUMBER DETECTED: The caller is calling from {self.clean_caller_phone}.
  When you reach STEP 3 (collecting phone number), ask naturally:
  "Should I register the appointment with the number you're calling from ({self.clean_caller_phone}), or would you like to use a different number?"
  * If they say yes, same number, or confirm (e.g. "yes", "this one", "haan isi number pe"), immediately use {self.clean_caller_phone}.
  * If they say no or give another number, use the new 10-digit number they speak.
"""

        # LLM Context with clinic knowledge & booking instructions
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d, %A")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d, %A")
        system_prompt = f"""You are "Gunjan", a warm, professional, and highly capable voice receptionist for {config.CLINIC_NAME}.

════════════════════════════════════════
CLINIC FACTS & PROFILE (Only use these facts)
════════════════════════════════════════
- Doctor: Dr. {config.DOCTOR_NAME}, Senior Physician & General Practitioner.
- Specialities: General health issues, fevers, seasonal infections, cold/cough, diabetes management, high blood pressure, gastric issues, routine checkups.
- Fee: ₹500 for a 30-minute consultation slot.
- Clinic Timings: Monday to Saturday, 9:00 AM to 5:00 PM. Closed on Sundays.
- Lunch Break: 12:00 PM to 1:00 PM (no slots).
- Walk-ins: Accepted, but booked patients receive priority.
- Cancellation / Rescheduling: Free of charge anytime.

DATE & CALENDAR CONTEXT:
- TODAY is {today_str} (Year: {now.year}).
- TOMORROW is {tomorrow_str}.
- When caller asks for "tomorrow" or any date, use {now.year}-MM-DD.

════════════════════════════════════════
APPOINTMENT WORKFLOW (5 steps, strictly in order)
════════════════════════════════════════
1. STEP 1 — SLOT INQUIRY: If the caller wants to book, call get_available_slots for the target date. State the available slots naturally and ask which time they prefer.
2. STEP 2 — COLLECT NAME: Once a time slot is chosen, ask for the patient's full name. DO NOT proceed to booking or ask for the phone number yet.
3. STEP 3 — COLLECT PHONE:{caller_num_text}
   * If incoming caller number ({self.clean_caller_phone or "available"}) is detected:
     Ask: "Should I register this appointment under the number you are calling from, or would you prefer a different mobile number?"
     - If the caller agrees (says "yes", "this number", "same number", "haan isi pe"): Use {self.clean_caller_phone}.
     - If the caller says "no" or provides a different number: Use the new 10-digit number they speak.
   * If no caller number is detected: Ask for their 10-digit mobile phone number.
4. STEP 4 — BOOK & CONFIRM: Call the book_appointment tool. Verbally confirm all registration details clearly (name, date, time, and registered mobile number).
5. STEP 5 — WRAP UP: Ask if they need any further help. If they say no or thank you, respond with a warm farewell. The call will automatically end 1 second after you say your farewell.

════════════════════════════════════════
CANCELLATION & RESCHEDULING FLEXIBILITY:
════════════════════════════════════════
- Patients can identify and cancel or reschedule their appointment using EITHER their full name OR their phone number.
- If a caller says they forgot their phone number, do not have it, or simply provides their name, ACCEPT THEIR NAME directly and call cancel_appointment or reschedule_appointment immediately using their patient_name. NEVER refuse cancellation or insist on a phone number if the patient's name is provided.
{caller_phone_instruction}
- SPEAKING TO DOCTOR: If caller asks to speak directly with Dr. Umang Chourasia (e.g. "mujhe doctor se baat karni hai", "I want to talk to doctor"), respond warmly: "Dr. Umang Chourasia is currently seeing patients in consultation. I can schedule a consultation slot for you right now so you can speak with him directly. Would you like to check available slots for today or tomorrow?"

════════════════════════════════════════
DYNAMIC VOICE & CONVERSATION RULES
════════════════════════════════════════
- DYNAMIC RESPONSES: Do NOT use pre-scripted or hardcoded reply templates. Respond dynamically, keeping the conversation natural, friendly, and human-like.
- LANGUAGE ADAPTABILITY: If the caller speaks Hindi or Hinglish, respond entirely in natural Hindi/Hinglish. If they speak English, respond in English.
- MEMORY RESILIENCY: Once the caller has chosen a date/time or provided their name, NEVER forget, clear, or request these details again in this call. If the phone number is invalid, too short, or fragmented, do NOT ask for the patient's name or date/time again. Maintain the confirmed name and slot in memory, and ask ONLY for the phone number. Additionally, if the caller repeats, restates, or corrects the date/time slot, but they have already provided the patient's name earlier in the conversation, do NOT ask for the name again. Skip directly to asking for the mobile number.
- NO RAW FORMATS IN SPEECH: NEVER ask the caller to speak dates in "YYYY-MM-DD" format or mention raw date formats. Let callers speak dates naturally (e.g. "tomorrow", "August 26th"). You must translate their natural spoken date into the YYYY-MM-DD format silently before calling tools.
- OUT-OF-SCOPE RULES:
  * If asked about specific medical advice, dosages, or prescriptions, tell the caller you cannot provide medical advice over the phone and suggest consulting the doctor during their visit.
  * If asked about completely unrelated topics (news, other businesses, general questions), politely redirect them to clinic queries.
  * Introductions, names, greetings, and slot queries are ALWAYS in-scope. Never refuse these.
- "Thank you" mid-booking is NOT a farewell. Do not say goodbye until the booking has been confirmed (Step 4).
- Speak times in standard 12-hour format (e.g. "9:00 AM", "1:30 PM").
- Keep replies concise (1–2 short spoken sentences).
- Do not use markdown (bolding, bullet points, asterisks) in your output.
"""
        self.chat_ctx = llm.ChatContext()
        self.chat_ctx.add_message(role="system", content=system_prompt)
        if caller_phone:
            logger.info(f"Incoming call detected from: {caller_phone} (Clean: {self.clean_caller_phone})")
        self.started_at = datetime.now()

        # Initialize Silero VAD matching agent.py configuration (16kHz neural inference)
        self.vad = silero.VAD.load(
            activation_threshold=0.60,
            min_silence_duration=0.60,
            prefix_padding_duration=0.30
        )
        self.vad_stream = self.vad.stream()

    async def run(self):
        # Start VAD reader background loop
        self.vad_reader_task = asyncio.create_task(self._vad_reader())

        # Gentle 650ms connection buffer so telephony carrier audio establishes before greeting plays
        await asyncio.sleep(0.65)

        # Speak initial greeting
        self.cancel_playback_flag = False
        self.is_speaking = True
        import time
        self.playout_start_time = time.perf_counter()
        greeting_text = f"Hello! Welcome to {config.CLINIC_NAME}. I am Gunjan, how may I help you today?"
        self.playout_task = asyncio.create_task(self._playout_stream(greeting_text))

    async def _vad_reader(self):
        from livekit.agents.vad import VADEventType
        try:
            async for event in self.vad_stream:
                if self.closed:
                    break
                if event.type == VADEventType.START_OF_SPEECH:
                    logger.info("VAD: Caller started speaking. Interrupting assistant speech.")
                    self.interrupt_playback()
                elif event.type == VADEventType.END_OF_SPEECH:
                    logger.info("VAD: Caller stopped speaking. Transcribing utterance...")
                    # Concatenate ALL 16kHz audio frames in the speech segment for full speech transcription
                    speech_pcm = b"".join(f.data for f in event.frames)
                    if not speech_pcm:
                        continue
                    frame = rtc.AudioFrame(
                        data=speech_pcm,
                        sample_rate=16000,
                        num_channels=1,
                        samples_per_channel=len(speech_pcm) // 2
                    )
                    # Cancel any running turn — always answer the LATEST question
                    if self.turn_lock.locked():
                        logger.info("Cancelling previous turn to handle new speech.")
                        for t in list(self.active_turn_tasks):
                            t.cancel()
                        # Give a moment for cancellation to propagate
                        await asyncio.sleep(0.05)
                    task = asyncio.create_task(self.process_turn(frame))
                    self.active_turn_tasks.add(task)
                    task.add_done_callback(lambda t: self.active_turn_tasks.discard(t))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in VAD reader loop: {e}", exc_info=True)

    def interrupt_playback(self):
        """Stops assistant speech when caller speaks, ignoring initial line clicks/pops."""
        import time
        if self.playout_start_time is not None:
            elapsed = time.perf_counter() - self.playout_start_time
            # Ignore line clicks/pops that occur within the first 350ms of playback
            if elapsed < 0.35:
                return
        self.cancel_playback_flag = True
        if self.playout_task and not self.playout_task.done():
            self.playout_task.cancel()

    async def say(self, text: str):
        self.cancel_playback_flag = False
        import time
        self.playout_start_time = time.perf_counter()
        self.is_speaking = True
        try:
            self.playout_task = asyncio.create_task(self._playout_stream(text))
            await self.playout_task
        finally:
            self.is_speaking = False

    async def _playout_mulaw(self, mulaw_data: bytes):
        """Stream native 8kHz mu-law audio to WebSocket with 100ms jitter buffer priming."""
        import time
        try:
            if self.cancel_playback_flag or not mulaw_data:
                return
            chunk_size = 640  # 640 bytes = 80ms audio chunk @ 8kHz mulaw
            start_pacing = time.perf_counter()
            packets_sent = 0

            for i in range(0, len(mulaw_data), chunk_size):
                if self.cancel_playback_flag:
                    break
                audio_chunk = mulaw_data[i:i + chunk_size]
                if len(audio_chunk) < chunk_size:
                    pad_bytes = chunk_size - len(audio_chunk)
                    audio_chunk = audio_chunk + (b'\xff' * pad_bytes)  # 0xff is mu-law silence

                payload_b64 = base64.b64encode(audio_chunk).decode("utf-8")
                await self.websocket.send(json.dumps({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {
                        "payload": payload_b64
                    }
                }))
                packets_sent += 1

                # Precise 80ms pacing (bypasses Windows timer lag and ngrok socket overhead)
                expected_time = start_pacing + (packets_sent * 0.08)
                sleep_duration = expected_time - time.perf_counter()
                if sleep_duration > 0.005:
                    await asyncio.sleep(sleep_duration)
        except asyncio.CancelledError:
            logger.info("Speech playout task interrupted by caller.")
        except Exception as e:
            from websockets.exceptions import ConnectionClosed
            if not isinstance(e, ConnectionClosed):
                logger.error(f"Error in speech playout: {e}")

    async def _playout_stream(self, text: str):
        """Synthesize native 8kHz mu-law speech via Cartesia and stream directly to WebSocket."""
        try:
            # Normalize text for speech (pronounce phone numbers and currencies cleanly)
            spoken_text = _normalize_text_for_speech(text)
            logger.info(f"Speaking: {text}")

            mulaw_bytes = None
            if config.CARTESIA_API_KEY:
                try:
                    session = await get_http_session()
                    cartesia_url = "https://api.cartesia.ai/tts/bytes"
                    c_headers = {
                        "X-API-Key": config.CARTESIA_API_KEY,
                        "Cartesia-Version": "2024-06-10",
                        "Content-Type": "application/json"
                    }
                    is_hi = _detect_hindi(spoken_text)
                    c_payload = {
                        "model_id": "sonic-2",
                        "transcript": spoken_text,
                        "language": "hi" if is_hi else "en",
                        "voice": {
                            "mode": "id",
                            "id": config.CARTESIA_VOICE_ID or "c6bbc7d5-4b35-4d49-b1c6-4417019a61c1"
                        },
                        "output_format": {
                            "container": "raw",
                            "encoding": "pcm_mulaw",
                            "sample_rate": 8000
                        }
                    }
                    async with session.post(cartesia_url, headers=c_headers, json=c_payload) as c_resp:
                        if c_resp.status == 200:
                            mulaw_bytes = await c_resp.read()
                        else:
                            err = await c_resp.text()
                            logger.warning(f"Cartesia TTS error {c_resp.status}: {err}")
                except Exception as c_err:
                    logger.warning(f"Cartesia TTS failed: {c_err}")

            if not mulaw_bytes:
                logger.error("No audio generated from Cartesia TTS.")
                return

            await self._playout_mulaw(mulaw_bytes)

        except asyncio.CancelledError:
            logger.info("Speech playout task interrupted by caller.")
        except Exception as e:
            from websockets.exceptions import ConnectionClosed
            if isinstance(e, ConnectionClosed):
                pass  # Call ended — ignore silently
            else:
                logger.error(f"Error in speech playout: {e}", exc_info=True)

    async def process_turn(self, audio_frame: rtc.AudioFrame):
        # Prevent parallel turns — if a turn is already running, skip this one
        if self.turn_lock.locked():
            logger.info("Turn already in progress, skipping duplicate process_turn.")
            return
        async with self.turn_lock:
            try:
                # 1. Speech-to-Text Transcription via Cartesia Ink-Whisper (with Groq backup)
                user_text = await transcribe_audio_via_cartesia(audio_frame.data)
                if not user_text and config.GROQ_API_KEY:
                    user_text = await transcribe_audio_via_groq(audio_frame.data)

                if not user_text or not user_text.strip():
                    logger.info("ASR: No speech recognized in utterance.")
                    return

                # Ignore filler utterances (e.g. 'um', 'uh', 'ah', 'hmm') so they don't trigger false interruptions
                clean_text = user_text.lower().strip(" .,!?:;")
                if clean_text in ["um", "uh", "ah", "hmm", "er", "oh", "eh", "hm"]:
                    logger.info(f"Ignoring filler utterance: '{user_text}'")
                    return

                logger.info(f"Caller Said: {user_text}")
                self.chat_ctx.add_message(role="user", content=user_text)

                # Reset flags — we are now the active turn, ready to respond
                self.cancel_playback_flag = False

                # 2. Fast LLM Generation with sub-300ms SSE streaming
                llm_stream = llm_provider.chat(chat_ctx=self.chat_ctx)
                full_response = ""

                async for chunk in llm_stream:
                    if self.cancel_playback_flag:
                        break
                    delta = chunk.text if hasattr(chunk, 'text') else ""
                    if delta:
                        full_response += delta

                full_response = full_response.strip()
                if full_response and not self.cancel_playback_flag:
                    self.chat_ctx.add_message(role="assistant", content=full_response)

                    # Synthesize and stream the complete response as one fluid, continuous audio playout
                    await self.say(full_response)

                    lower_resp = full_response.lower()

                    # Track booking confirmation: set confirmed=True when Gunjan speaks the booking confirmation
                    if any(kw in lower_resp for kw in ["is confirmed", "appointment is booked", "registered your mobile", "registered the number", "booked on", "booked for"]):
                        self.booking_state["confirmed"] = True
                        logger.info("Booking state: CONFIRMED")

                    # Track that booking flow has started
                    if any(kw in lower_resp for kw in ["slot", "available", "full name", "patient's name", "mobile number", "10-digit"]):
                        self.booking_state["active"] = True

                    # Detect caller's wrap-up intent from their INPUT text
                    caller_done_signals = [
                        "no", "nahi", "nothing", "nope", "that's all", "thats all",
                        "nothing else", "i'm good", "im good", "all good", "bas",
                        "no thanks", "no thank you", "thank you", "thanks", "shukriya",
                        "bye", "goodbye", "alvida", "ok bye", "okay bye"
                    ]
                    clean_user = user_text.lower().strip(" .,!?")
                    caller_wants_to_end = (
                        self.booking_state["confirmed"] and
                        any(clean_user == sig or clean_user.startswith(sig) for sig in caller_done_signals)
                    )

                    # Determine if this is a genuine farewell response
                    farewell_keywords = ["goodbye", "have a wonderful day", "have a great day", "take care and have",
                                         "take care. goodbye", "shubh din", "alvida", "it was my pleasure"]
                    is_farewell_resp = any(kw in lower_resp for kw in farewell_keywords)
                    booking_in_progress = self.booking_state["active"] and not self.booking_state["confirmed"]

                    should_hangup = (
                        is_farewell_resp and not booking_in_progress
                    ) or caller_wants_to_end

                    if should_hangup:
                        logger.info("Call wrap-up detected. Waiting for farewell audio playout to finish...")
                        if self.playout_task and not self.playout_task.done():
                            try:
                                await self.playout_task
                            except Exception:
                                pass
                        await asyncio.sleep(1.2)  # Buffer so caller hears the complete farewell word
                        try:
                            await self.websocket.close()
                        except Exception:
                            pass
                    elif is_farewell_resp and booking_in_progress:
                        logger.warning("Farewell keyword in response but booking still in progress — suppressing hangup.")

            except Exception as e:
                logger.error(f"Error in process_turn: {e}", exc_info=True)

    async def close(self):
        logger.info(f"Cleaning up call session. StreamSid: {self.stream_sid}")
        self.closed = True
        self.interrupt_playback()
        # Cancel all active process_turn tasks
        for task in list(self.active_turn_tasks):
            task.cancel()
        if self.vad_reader_task:
            self.vad_reader_task.cancel()
        try:
            self.vad_stream.end_input()
            await self.vad_stream.aclose()
        except Exception:
            pass

        # Save complete call transcript to database as structured JSON
        try:
            turns = []
            turn_idx = 1
            for msg in self.chat_ctx.messages():
                if msg.role != "system":
                    role = "user" if msg.role == "user" else "assistant"
                    speaker = "Caller" if msg.role == "user" else "Gunjan (AI Receptionist)"
                    text = _extract_msg_text(msg.content).strip()
                    if text:
                        turns.append({
                            "turn": turn_idx,
                            "speaker": speaker,
                            "role": role,
                            "text": text
                        })
                        turn_idx += 1

            if turns:
                duration_sec = int((datetime.now() - self.started_at).total_seconds()) if self.started_at else 0
                transcript_payload = {
                    "session_id": self.stream_sid,
                    "caller_phone": self.caller_phone or "Unknown",
                    "started_at": self.started_at.isoformat() if self.started_at else datetime.now().isoformat(),
                    "ended_at": datetime.now().isoformat(),
                    "duration_seconds": duration_sec,
                    "clinic_name": config.CLINIC_NAME,
                    "doctor_name": config.DOCTOR_NAME,
                    "turns_count": len(turns),
                    "turns": turns
                }
                json_transcript_str = json.dumps(transcript_payload, indent=2, ensure_ascii=False)

                appointments_db.log_call_session(
                    session_id=self.stream_sid,
                    caller_phone=self.caller_phone or "Unknown",
                    started_at=self.started_at,
                    ended_at=datetime.now(),
                    transcript=json_transcript_str,
                    summary=f"Call completed with {len(turns)} turns ({duration_sec}s)",
                    outcome="COMPLETED"
                )
                logger.info(f"Saved complete JSON call transcript ({len(turns)} turns) to call_sessions (Session {self.stream_sid})")
        except Exception as e:
            logger.error(f"Failed to log call session to database: {e}")


# ----- WebSocket Server Connection Handler -----
async def handler(websocket):
    logger.info("Incoming WebSocket connection from Smartflo...")
    session = None
    try:
        import websockets
        async for message in websocket:
            data = json.loads(message)
            event = data.get("event")

            if event == "connected":
                logger.info("Tata Smartflo connection handshake completed.")
            elif event == "start":
                start_data = data.get("start", {})
                stream_sid = data.get("streamSid") or start_data.get("streamSid") or "stream-1"
                caller_phone = start_data.get("from") or start_data.get("caller") or ""
                logger.info(f"Start event received. StreamSid: {stream_sid}, From: {caller_phone}")
                session = VoiceSession(websocket, stream_sid, caller_phone)
                await session.run()
            elif event == "media":
                if session:
                    payload = base64.b64decode(data["media"]["payload"])
                    # Convert 8kHz µ-law payload to 8kHz 16-bit PCM
                    pcm_8k = audioop.ulaw2lin(payload, 2)
                    # Upsample 8kHz PCM -> 16kHz PCM for Silero VAD neural model
                    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
                    frame = rtc.AudioFrame(
                        data=pcm_16k,
                        sample_rate=16000,
                        num_channels=1,
                        samples_per_channel=len(pcm_16k) // 2
                    )
                    session.vad_stream.push_frame(frame)
            elif event == "stop":
                logger.info("Tata Smartflo sent stop event. Hanging up.")
                break
    except websockets.exceptions.ConnectionClosed:
        logger.info("WebSocket connection closed by Smartflo.")
    except Exception as e:
        logger.error(f"Error in WebSocket handler loop: {e}", exc_info=True)
    finally:
        if session:
            await session.close()


# ----- Main Server Entrypoint -----
async def main():
    import websockets
    # Initialize Postgres DB Schema once at server startup
    logger.info("Initializing Postgres database tables...")
    try:
        appointments_db.initialize_db()
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}")

    # Initialize AI models (VAD, STT, LLM, TTS)
    initialize_providers()

    # Pre-render initial greeting to eliminate startup greeting delay
    await precompute_greeting()

    logger.info("Starting Tata Smartflo WebSocket Server on port 8080...")
    async with websockets.serve(handler, "0.0.0.0", 8080):
        await asyncio.Future()  # Keep running indefinitely


if __name__ == "__main__":
    # Ensure event loop exists
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    loop.run_until_complete(main())

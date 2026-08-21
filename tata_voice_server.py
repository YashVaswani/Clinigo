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

    # 2. Convert 24-hr times (e.g. 13:00 -> 1:00 PM, 09:30 -> 9:30 AM) only if not already followed by AM/PM
    def format_time(m):
        hh, mm = int(m.group(1)), m.group(2)
        period = 'AM' if hh < 12 else 'PM'
        display_h = hh if 1 <= hh <= 12 else (hh - 12 if hh > 12 else 12)
        return f'{display_h}:{mm} {period}'
    text = re.sub(r'\b([01]?\d|2[0-3]):([0-5]\d)(?!\s*(?:AM|PM|am|pm))\b', format_time, text)

    # 3. Convert ₹500 / Rs 500 to spoken words
    is_hindi = _detect_hindi(text)
    curr_word = "रुपये" if is_hindi else "rupees"
    text = re.sub(r'[₹|Rs\.?]\s*(\d+)', rf'\1 {curr_word}', text)

    # 4. Format phone numbers (7 to 13 digits) into spaced digits
    def format_phone(m):
        digits = m.group(0)
        if len(digits) == 10:
            return " ".join(list(digits))
        return " ".join(list(digits))
    text = re.sub(r'\b\d{7,13}\b', format_phone, text)

    # 5. Phonetic pronunciation enhancements for Cartesia TTS
    text = re.sub(r'\bGunjan\b', 'Goonjan', text)

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


async def transcribe_audio_via_groq(pcm_bytes: bytes, api_key: str) -> str:
    """Transcribe 8kHz 16-bit PCM audio using Groq Whisper API."""
    import io, wave
    # Wrap raw PCM in a WAV container
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(8000)
        wf.writeframes(pcm_bytes)
    wav_buf.seek(0)
    wav_bytes = wav_buf.read()

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", "whisper-large-v3-turbo")
        form.add_field("response_format", "text")
        # Add prompt hint biasing toward clinic vocabulary (prevents 'cancel' -> 'cancer' phonetic confusion)
        form.add_field("prompt", "Healthcare clinic reception call with Gunjan and Dr. Umang Chourasia to book, check available slots, reschedule, or cancel appointment.")
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, data=form) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return text.strip()
                elif resp.status == 429:
                    logger.warning("Groq Whisper rate limited (429). Waiting 2s and retrying...")
                    await asyncio.sleep(2)
                    async with session.post(url, headers=headers, data=form) as resp2:
                        if resp2.status == 200:
                            return (await resp2.text()).strip()
                else:
                    logger.warning(f"Groq Whisper returned {resp.status}: {await resp.text()}")
    except Exception as e:
        logger.error(f"Error in Groq Whisper transcription: {e}")
    return ""


async def transcribe_audio_via_gemini(pcm_bytes: bytes, api_key: str) -> str:
    """Fallback: transcribe using Gemini multimodal API."""
    wav_bytes = pcm_to_wav(pcm_bytes, 8000)
    b64_data = base64.b64encode(wav_bytes).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [
                {"inlineData": {"mimeType": "audio/wav", "data": b64_data}},
                {"text": "Transcribe the spoken audio precisely. If there is no speech or only noise, reply with an empty string. Otherwise, output only the transcribed text."}
            ]
        }]
    }
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        return "".join([p.get("text", "") for p in parts]).strip()
                else:
                    logger.warning(f"Gemini ASR returned code {resp.status}")
    except Exception as e:
        logger.error(f"Error in Gemini transcription: {e}")
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


# ----- Edge TTS Fallback Class -----
class EdgeTTS(tts.TTS):
    VOICE_HINDI   = "hi-IN-SwaraNeural"
    VOICE_ENGLISH = "en-IN-NeerjaNeural"
    SAMPLE_RATE   = 24000

    def __init__(self):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self.SAMPLE_RATE,
            num_channels=1,
        )

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = None) -> tts.ChunkedStream:
        voice = self.VOICE_HINDI if _detect_hindi(text) else self.VOICE_ENGLISH
        opts = conn_options or DEFAULT_API_CONNECT_OPTIONS
        return EdgeTTSChunkedStream(
            tts=self,
            input_text=text,
            conn_options=opts,
            voice=voice,
        )


class EdgeTTSChunkedStream(tts.ChunkedStream):
    def __init__(self, tts, input_text, conn_options, voice):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._voice = voice

    async def _run(self, output_emitter) -> None:
        import io
        import miniaudio
        import edge_tts as etts

        try:
            mp3_buf = io.BytesIO()
            communicate = etts.Communicate(self.input_text, self._voice)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    mp3_buf.write(chunk["data"])

            mp3_bytes = mp3_buf.getvalue()
            if not mp3_bytes:
                logger.error("EdgeTTS: No audio data received from edge-tts")
                return

            decoded = miniaudio.decode(mp3_bytes, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
            raw_pcm = bytes(decoded.samples)

            sr   = decoded.sample_rate
            ch   = decoded.nchannels
            bps  = 2  # 16-bit
            chunk_size = int(sr * 0.02 * bps * ch)

            import uuid
            output_emitter.start_segment(segment_id=str(uuid.uuid4()))
            for i in range(0, len(raw_pcm), chunk_size):
                piece = raw_pcm[i: i + chunk_size]
                if not piece:
                    continue
                if len(piece) < chunk_size:
                    piece = piece + b'\x00' * (chunk_size - len(piece))
                output_emitter.push(piece)
            output_emitter.end_segment()

        except Exception as e:
            logger.error(f"Error in EdgeTTS stream: {e}")


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


# ----- Clinic Appointment Tools Definition for Gemini -----
CLINIC_TOOLS = [{
    "functionDeclarations": [
        {
            "name": "get_available_slots",
            "description": "Check available 30-min appointment slots for Dr. Umang Chourasia on a specific date (YYYY-MM-DD)",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "target_date": {"type": "STRING", "description": "Date in YYYY-MM-DD format (e.g. 2026-08-22)"}
                },
                "required": ["target_date"]
            }
        },
        {
            "name": "book_appointment",
            "description": "Book a clinic appointment for a patient with Dr. Umang Chourasia",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "patient_name": {"type": "STRING", "description": "Full name of the patient"},
                    "patient_phone": {"type": "STRING", "description": "Phone number of the patient"},
                    "appointment_date": {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                    "appointment_time": {"type": "STRING", "description": "Time in HH:MM format (24-hour, e.g. 10:00 or 14:30)"}
                },
                "required": ["patient_name", "patient_phone", "appointment_date", "appointment_time"]
            }
        },
        {
            "name": "cancel_appointment",
            "description": "Cancel an existing appointment for a patient",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "patient_name": {"type": "STRING", "description": "Full name of the patient"},
                    "patient_phone": {"type": "STRING", "description": "Phone number of the patient"},
                    "appointment_date": {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                    "appointment_time": {"type": "STRING", "description": "Time in HH:MM format (optional)"}
                },
                "required": ["patient_name", "patient_phone", "appointment_date"]
            }
        },
        {
            "name": "reschedule_appointment",
            "description": "Reschedule an existing appointment to a new date and time",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "patient_name": {"type": "STRING", "description": "Full name of the patient"},
                    "patient_phone": {"type": "STRING", "description": "Phone number of the patient"},
                    "old_date": {"type": "STRING", "description": "Original appointment date in YYYY-MM-DD format"},
                    "old_time": {"type": "STRING", "description": "Original appointment time in HH:MM format"},
                    "new_date": {"type": "STRING", "description": "New appointment date in YYYY-MM-DD format"},
                    "new_time": {"type": "STRING", "description": "New appointment time in HH:MM format"}
                },
                "required": ["patient_name", "patient_phone", "old_date", "old_time", "new_date", "new_time"]
            }
        },
        {
            "name": "get_appointment_details",
            "description": "Look up existing appointments for a patient by name or phone number",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "patient_name": {"type": "STRING", "description": "Full name of the patient"},
                    "patient_phone": {"type": "STRING", "description": "Phone number of the patient"}
                }
            }
        }
    ]
}]


def _execute_clinic_tool(func_name: str, args: dict) -> dict:
    """Executes the requested appointment database tool and returns a result dict."""
    try:
        if func_name == "get_available_slots":
            date_str = args.get("target_date")
            slots = appointments_db.get_available_slots(date_str)
            return {"target_date": date_str, "available_slots": slots, "count": len(slots)}
        elif func_name == "book_appointment":
            return appointments_db.book_appointment(
                name=args.get("patient_name"),
                phone=args.get("patient_phone"),
                date_str=args.get("appointment_date"),
                time_str=args.get("appointment_time")
            )
        elif func_name == "cancel_appointment":
            return appointments_db.cancel_appointment(
                name=args.get("patient_name"),
                phone=args.get("patient_phone"),
                date_str=args.get("appointment_date"),
                time_str=args.get("appointment_time")
            )
        elif func_name == "reschedule_appointment":
            return appointments_db.reschedule_appointment(
                name=args.get("patient_name"),
                phone=args.get("patient_phone"),
                old_date_str=args.get("old_date"),
                old_time_str=args.get("old_time"),
                new_date_str=args.get("new_date"),
                new_time_str=args.get("new_time")
            )
        elif func_name == "get_appointment_details":
            query = args.get("patient_phone") or args.get("patient_name") or args.get("search_query") or ""
            return appointments_db.get_appointment_details(search_query=query)
    except Exception as e:
        logger.error(f"Error executing tool {func_name}: {e}")
        return {"error": str(e)}
    return {"error": f"Unknown tool: {func_name}"}


class LLMTextChunk:
    """Simple wrapper for a streamed text delta."""
    def __init__(self, text: str):
        self.text = text


class GeminiToolLLMStream:
    """Generates LLM responses with automatic Gemini Function Calling tool execution."""
    def __init__(self, chat_ctx: llm.ChatContext, api_key: str, model: str = "gemini-3.5-flash-lite"):
        self._chat_ctx = chat_ctx
        self._api_key = api_key
        self._model = model

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:streamGenerateContent?alt=sse&key={self._api_key}"
        headers = {"Content-Type": "application/json"}

        # Format system instruction and contents from chat context
        system_parts = []
        contents = []
        for msg in self._chat_ctx.messages():
            role = msg.role
            text = _extract_msg_text(msg.content)
            if role == "system":
                if text.strip():
                    system_parts.append({"text": text.strip()})
            else:
                api_role = "user" if role == "user" else "model"
                contents.append({"role": api_role, "parts": [{"text": text}]})

        try:
            timeout = aiohttp.ClientTimeout(total=15, connect=4.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                cur_contents = list(contents)
                
                # Multi-step tool execution loop (up to 4 steps)
                for step in range(4):
                    payload = {
                        "contents": cur_contents,
                        "tools": CLINIC_TOOLS
                    }
                    if system_parts:
                        payload["systemInstruction"] = {"parts": system_parts}

                    func_call = None
                    cand_parts = []

                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            logger.error(f"Gemini API error {resp.status}: {err_text}")
                            yield LLMTextChunk("I'm sorry, I'm having a brief issue. How may I assist you?")
                            return
                        
                        async for line in resp.content:
                            line_str = line.decode("utf-8", errors="replace").strip()
                            if line_str.startswith("data:"):
                                try:
                                    data = json.loads(line_str[5:].strip())
                                    cands = data.get("candidates", [])
                                    if cands:
                                        parts = cands[0].get("content", {}).get("parts", [])
                                        for p in parts:
                                            cand_parts.append(p)
                                            if "functionCall" in p:
                                                func_call = p["functionCall"]
                                            if "text" in p and p["text"]:
                                                yield LLMTextChunk(p["text"])
                                except Exception:
                                    pass

                    if func_call:
                        func_name = func_call.get("name")
                        func_args = func_call.get("args", {})
                        logger.info(f"LLM Tool Call: {func_name}({func_args})")
                        
                        # Execute tool against PostgreSQL
                        tool_result = _execute_clinic_tool(func_name, func_args)
                        logger.info(f"LLM Tool Result: {tool_result}")

                        # Append model functionCall and user functionResponse
                        cur_contents.append({"role": "model", "parts": cand_parts})
                        cur_contents.append({
                            "role": "user",
                            "parts": [{
                                "functionResponse": {
                                    "name": func_name,
                                    "response": {
                                        "name": func_name,
                                        "content": tool_result
                                    }
                                }
                            }]
                        })
                    else:
                        break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in GeminiToolLLMStream: {e}", exc_info=True)


class GeminiToolLLM(llm.LLM):
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite"):
        super().__init__()
        self._api_key = api_key
        self._model = model

    def chat(self, *, chat_ctx: llm.ChatContext, conn_options=None) -> GeminiToolLLMStream:
        return GeminiToolLLMStream(chat_ctx=chat_ctx, api_key=self._api_key, model=self._model)


# ----- Global Provider Instances -----
stt_provider = None
tts_provider = None
llm_provider = None


def initialize_providers():
    global llm_provider

    # 1. STT Provider Info
    logger.info("Initializing Groq Whisper STT (whisper-large-v3-turbo via aiohttp)...")

    # 2. TTS Provider Info
    if config.CARTESIA_API_KEY:
        logger.info(f"Initializing Cartesia TTS (sonic-turbo | Voice: {config.CARTESIA_VOICE_ID or 'Lavanya'})...")
    else:
        logger.info("Initializing EdgeTTS fallback (hi-IN-SwaraNeural / en-IN-NeerjaNeural)...")

    # 3. LLM Provider — Gemini 3.5 Flash Lite with Clinic DB Function Calling
    logger.info("Initializing Gemini Tool LLM (gemini-3.5-flash-lite)...")
    llm_provider = GeminiToolLLM(
        api_key=config.GOOGLE_API_KEY,
        model="gemini-3.5-flash-lite"
    )


# ----- Session Instance to Manage Calls -----
class VoiceSession:
    def __init__(self, websocket, stream_sid: str, caller_phone: str):
        self.websocket = websocket
        self.stream_sid = stream_sid
        self.caller_phone = caller_phone
        self.is_speaking = False
        self.chunk_counter = 1
        self.cancel_playback_flag = False
        self.closed = False
        self.playout_task = None
        self.vad_reader_task = None
        self.active_turn_tasks: list[asyncio.Task] = []
        self.turn_lock = asyncio.Lock()  # Prevents parallel process_turn calls

        # Build prompt
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d, %A")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d, %A")
        system_prompt = f"""You are "Gunjan", the smart, warm, and highly capable medical receptionist for {config.CLINIC_NAME} (Doctor: {config.DOCTOR_NAME}).

CLINIC & DOCTOR PROFILE:
- Doctor: Dr. {config.DOCTOR_NAME}, Senior Physician & General Specialist.
- Expertise: General medicine, fevers, infections, seasonal illnesses, cough/cold, diabetes management, hypertension/BP, gastric issues, routine health checkups, and chronic condition consultations.
- Consultation Fee: ₹500 for a thorough 30-minute in-clinic consultation.
- Working Hours: Monday to Saturday, 9:00 AM to 5:00 PM (Lunch break: 12:00 PM to 1:00 PM). Closed on Sundays.
- Slot Duration: 30 minutes per appointment to ensure dedicated doctor attention.
- Location: {config.CLINIC_NAME}, Main Medical Complex.
- Walk-ins: Walk-ins are accepted but booked appointments receive zero waiting priority.
- Cancellation / Reschedule: Completely free of charge anytime.

DATE & CALENDAR CONTEXT:
- TODAY is {today_str} (Year: {now.year}).
- TOMORROW is {tomorrow_str}.
- Current Year is {now.year}. When the caller asks for "tomorrow" or any date, ALWAYS use {now.year}-MM-DD.

SMART RECEPTIONIST CAPABILITIES:
- URGENT CARE & SYMPTOMS: If a patient mentions urgent discomfort, fever, or needs immediate attention:
  1. Immediately check and offer the earliest vacant slot today or tomorrow with Dr. {config.DOCTOR_NAME}.
  2. If they cannot make that slot, ask their preferred timing and check alternative available slots.
  3. If no clinic timings work for their urgent timeline, or if it is an acute critical emergency (e.g. severe chest pain or breathlessness), advise visiting the nearest hospital emergency room.
- INQUIRIES & FAQS: Answer questions regarding doctor qualifications, fees (₹500), clinic timings, duration (30 mins), and location promptly and warmly.

PRIMARY APPOINTMENT WORKFLOW:
1. Check Slots: Call 'get_available_slots'. Always present slot timings clearly in English (e.g. "We have appointments from 9:00 AM to 12:00 PM in the morning, and 1:00 PM to 5:00 PM in the afternoon in 30-minute slots. What time works best for you?").
2. Collect Booking Details:
   - Patient Full Name.
   - Preferred Slot Time.
   - Contact Phone Number: Indian mobile numbers are 10 digits. Ask what 10-digit number they want to use for the booking. If they provide a new number, use it.
3. Confirm & Book: Call 'book_appointment' with name, date, time, and 10-digit phone number.
4. Rescheduling: When a patient asks to change or shift their slot, call 'reschedule_appointment' immediately with their name, phone, old date/time, and new date/time.
5. Post-Action Confirmation:
   - Once booked, rescheduled, or cancelled, clearly confirm the complete details (date, time, patient name, and recite the contact phone number).
   - Then always ask: "Is there anything else I can help you with today?"
6. Warm Call Wrap-up:
   - If the caller says "no", "that's all", "thank you", "kuch nahi", or indicates they are done, reply with a warm closing farewell:
     "Thank you for calling Healthcare Clinic! Take care and have a wonderful day. Goodbye!" (or in Hindi: "Healthcare Clinic me call karne ke liye dhanyawad! Apna khayal rakhiyega aur shubh din. Namaste!").
7. Manage Appointments: Use 'get_appointment_details' to search and 'cancel_appointment' to cancel.

CRITICAL VOICE & CONVERSATION RULES:
- TIMINGS IN ENGLISH: Always speak slot timings in English (e.g. "9:00 AM", "1:30 PM", "4:00 PM") even during Hindi/Hinglish conversations, as medical appointment times are universally clearer in English.
- NO UNPROMPTED PHONE RECITAL: Do NOT randomly read out or spell out phone numbers during general conversation or inquiries. Only read the phone number when final booking confirmation is reached.
- RESPECT USER PHONE PREFERENCE: Always ask the caller what phone number they want to register for the booking. If they provide a new number or say they want to change it, immediately use the new number.
- NATURAL INTERRUPTION HANDLING: If the caller says "continue", "go ahead", "yes", or "okay" after an interruption, simply continue your previous thought naturally. Do not misinterpret "continue" as a command to change language or restart.
- NATURAL MULTILINGUAL & HINGLISH: Match the caller's natural language. If the caller speaks English, reply in English. If the caller speaks Hindi, reply in Hindi. If Hinglish, reply in natural Hinglish.
- REPEAT REQUESTS: If the caller says "repeat that", "what did you say?", "pardon?", "can you repeat?", "dobara boliye", "phir se kahiye", or asks to repeat the previous sentence or details, politely and clearly repeat the last information you stated.
- NO MARKDOWN: Never use asterisks (*), bold, or bullet points in your spoken responses.
- NO REPEATED GREETINGS: Do not say "Hello" or "Namaste" in subsequent turns after the call has already begun.
- Keep replies concise (1-2 clear, spoken sentences).
"""
        self.chat_ctx = llm.ChatContext()
        self.chat_ctx.add_message(role="system", content=system_prompt)
        if caller_phone:
            logger.info(f"Incoming call detected from: {caller_phone}")
            self.chat_ctx.add_message(
                role="system",
                content=f"Note: Caller ID is {caller_phone}. Ask the caller what phone number they'd like to use for the booking (they may use this caller ID or provide a different contact number)."
            )
        self.started_at = datetime.now()

        # Initialize Silero VAD (8kHz matches Smartflo audio stream rate, 0.20s min silence for super-fast turn-taking)
        self.vad = silero.VAD.load(
            sample_rate=8000,
            activation_threshold=0.55,
            min_silence_duration=0.20
        )
        self.vad_stream = self.vad.stream()

    async def run(self):
        # Start VAD reader background loop
        self.vad_reader_task = asyncio.create_task(self._vad_reader())

        # Speak initial greeting immediately upon connection
        greeting_text = f"Hello! Welcome to {config.CLINIC_NAME}. I am Gunjan, how may I help you today?"
        asyncio.create_task(self.say(greeting_text))

    async def _vad_reader(self):
        from livekit.agents.vad import VADEventType
        try:
            async for event in self.vad_stream:
                if self.closed:
                    break
                if event.type == VADEventType.START_OF_SPEECH:
                    logger.info("VAD: Caller started speaking. Interrupting assistant speech.")
                    self.is_speaking = True
                    self.interrupt_playback()
                elif event.type == VADEventType.END_OF_SPEECH:
                    logger.info("VAD: Caller stopped speaking. Transcribing utterance...")
                    self.is_speaking = False
                    frame = event.frames[0]
                    # Cancel any running turn — always answer the LATEST question
                    if self.turn_lock.locked():
                        logger.info("Cancelling previous turn to handle new speech.")
                        for t in list(self.active_turn_tasks):
                            t.cancel()
                        # Give a moment for cancellation to propagate
                        await asyncio.sleep(0.05)
                    task = asyncio.create_task(self.process_turn(frame))
                    self.active_turn_tasks.append(task)
                    task.add_done_callback(lambda t: self.active_turn_tasks.remove(t) if t in self.active_turn_tasks else None)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in VAD reader loop: {e}", exc_info=True)

    def interrupt_playback(self):
        self.cancel_playback_flag = True
        if self.playout_task and not self.playout_task.done():
            self.playout_task.cancel()

    async def say(self, text: str):
        self.interrupt_playback()
        self.cancel_playback_flag = False
        self.playout_task = asyncio.create_task(self._playout_stream(text))
        await self.playout_task

    async def _playout_stream(self, text: str):
        """Generate speech via edge_tts directly and stream mulaw audio to the WebSocket."""
        import io
        import miniaudio
        import edge_tts as etts

        try:
            # Normalize text for speech (pronounce phone numbers and currencies cleanly)
            spoken_text = _normalize_text_for_speech(text)
            logger.info(f"Speaking: {text}")

            # 1. Synthesize audio: Cartesia Sonic-Turbo (Primary) -> EdgeTTS (Fallback)
            raw_pcm = None
            if config.CARTESIA_API_KEY:
                try:
                    cartesia_url = "https://api.cartesia.ai/tts/bytes"
                    c_headers = {
                        "X-API-Key": config.CARTESIA_API_KEY,
                        "Cartesia-Version": "2024-06-10",
                        "Content-Type": "application/json"
                    }
                    c_payload = {
                        "model_id": "sonic-turbo",
                        "transcript": spoken_text,
                        "voice": {
                            "mode": "id",
                            "id": config.CARTESIA_VOICE_ID or "c6bbc7d5-4b35-4d49-b1c6-4417019a61c1",
                            "__experimental_controls": {
                                "speed": 0.92  # Comfortable, relaxed, clear conversational pacing
                            }
                        },
                        "output_format": {
                            "container": "raw",
                            "encoding": "pcm_s16le",
                            "sample_rate": 16000
                        }
                    }
                    timeout = aiohttp.ClientTimeout(total=5)
                    async with aiohttp.ClientSession(timeout=timeout) as c_session:
                        async with c_session.post(cartesia_url, headers=c_headers, json=c_payload) as c_resp:
                            if c_resp.status == 200:
                                pcm_16k = await c_resp.read()
                                raw_pcm, _ = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, None)
                            else:
                                err = await c_resp.text()
                                logger.warning(f"Cartesia TTS error {c_resp.status}: {err}. Falling back to EdgeTTS.")
                except Exception as c_err:
                    logger.warning(f"Cartesia TTS failed: {c_err}. Falling back to EdgeTTS.")

            # Fallback to EdgeTTS if Cartesia was not used or failed
            if not raw_pcm:
                VOICE_HINDI   = "hi-IN-SwaraNeural"
                VOICE_ENGLISH = "en-IN-NeerjaNeural"
                voice = VOICE_HINDI if _detect_hindi(spoken_text) else VOICE_ENGLISH

                mp3_buf = io.BytesIO()
                communicate = etts.Communicate(spoken_text, voice, rate="-10%")
                async for chunk in communicate.stream():
                    if self.cancel_playback_flag:
                        return
                    if chunk.get("type") == "audio":
                        mp3_buf.write(chunk["data"])

                mp3_bytes = mp3_buf.getvalue()
                if not mp3_bytes:
                    logger.error("EdgeTTS: No audio data received")
                    return

                decoded = miniaudio.decode(
                    mp3_bytes,
                    output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=1,
                    sample_rate=8000
                )
                raw_pcm = bytes(decoded.samples)

            if self.cancel_playback_flag:
                return

            # 2. Convert native 8kHz 16-bit PCM → 8-bit µ-law
            mulaw_data = audioop.lin2ulaw(raw_pcm, 2)

            # 4. Send in 40ms (320-byte) mulaw packets with wall-clock pacing to eliminate jitter
            import time
            chunk_size = 320
            start_pacing = time.perf_counter()
            packets_sent = 0

            for i in range(0, len(mulaw_data), chunk_size):
                if self.cancel_playback_flag:
                    break
                audio_chunk = mulaw_data[i:i + chunk_size]
                if len(audio_chunk) < chunk_size:
                    audio_chunk = audio_chunk + b'\xff' * (chunk_size - len(audio_chunk))

                payload_b64 = base64.b64encode(audio_chunk).decode("utf-8")
                await self.websocket.send(json.dumps({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {
                        "payload": payload_b64,
                        "chunk": str(self.chunk_counter)
                    }
                }))
                self.chunk_counter += 1
                packets_sent += 1

                # Precise wall-clock sleep
                expected_time = start_pacing + (packets_sent * 0.04)
                sleep_duration = expected_time - time.perf_counter()
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)

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
            # 1. Speech-to-Text Transcription
            # 1. Speech-to-Text Transcription via Groq Whisper (with Gemini backup)
            user_text = await transcribe_audio_via_groq(audio_frame.data, config.GROQ_API_KEY)
            if not user_text and config.GOOGLE_API_KEY:
                logger.info("Groq Whisper returned empty. Trying Gemini ASR as backup...")
                user_text = await transcribe_audio_via_gemini(audio_frame.data, config.GOOGLE_API_KEY)

            if not user_text.strip():
                logger.info("ASR: No speech detected.")
                return

            logger.info(f"Caller Said: {user_text}")
            self.chat_ctx.add_message(role="user", content=user_text)

            # Reset flags — we are now the active turn, ready to respond
            self.cancel_playback_flag = False

            # 2. Sentence-Pipelined LLM Generation & Speech (starts speaking immediately on 1st sentence)
            llm_stream = llm_provider.chat(chat_ctx=self.chat_ctx)
            full_response = ""
            current_sentence = ""
            import re

            async for chunk in llm_stream:
                if self.cancel_playback_flag:
                    break
                delta = chunk.text if hasattr(chunk, 'text') else ""
                if not delta:
                    continue
                full_response += delta
                current_sentence += delta

                # Check if we completed a sentence boundary (protect honorifics and buffer short < 3 word fragments)
                sentence_boundary_regex = r'(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bProf)(?<!\betc)[.!?।\n]+\s*'
                match = re.search(sentence_boundary_regex, current_sentence)
                if match:
                    split_idx = match.end()
                    sentence_to_say = current_sentence[:split_idx].strip()
                    # Only split if sentence is meaningful (> 2 words), avoiding 1-word fragments like 'Hello!'
                    if len(sentence_to_say.split()) >= 3:
                        current_sentence = current_sentence[split_idx:]
                        if sentence_to_say and not self.cancel_playback_flag:
                            await self.say(sentence_to_say)

            # Speak any trailing text
            if current_sentence.strip() and not self.cancel_playback_flag:
                await self.say(current_sentence.strip())

            if full_response.strip():
                self.chat_ctx.add_message(role="assistant", content=full_response)


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

        # Save complete call transcript to Supabase call_sessions table
        try:
            transcript_lines = []
            for msg in self.chat_ctx.messages():
                if msg.role != "system":
                    speaker = "Caller" if msg.role == "user" else "Gunjan (Agent)"
                    text = _extract_msg_text(msg.content)
                    if text.strip():
                        transcript_lines.append(f"{speaker}: {text.strip()}")
            
            full_transcript = "\n\n".join(transcript_lines)
            if full_transcript.strip():
                appointments_db.log_call_session(
                    session_id=self.stream_sid,
                    caller_phone=self.caller_phone or "Unknown",
                    started_at=self.started_at,
                    ended_at=datetime.now(),
                    transcript=full_transcript,
                    summary=f"Call completed with {len(transcript_lines)} turns",
                    outcome="COMPLETED"
                )
                logger.info(f"Saved complete call transcript to Supabase call_sessions (Session {self.stream_sid})")
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
                stream_sid = data["streamSid"]
                caller_phone = data["start"].get("from", "")
                session = VoiceSession(websocket, stream_sid, caller_phone)
                await session.run()
            elif event == "media":
                if session:
                    payload = base64.b64decode(data["media"]["payload"])
                    # Convert 8kHz µ-law payload to 8kHz 16-bit PCM
                    pcm = audioop.ulaw2lin(payload, 2)
                    frame = rtc.AudioFrame(
                        data=pcm,
                        sample_rate=8000,
                        num_channels=1,
                        samples_per_channel=len(pcm) // 2
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

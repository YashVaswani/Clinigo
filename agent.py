import logging
from datetime import datetime, date
import os
from typing import Annotated, AsyncIterator
import asyncio
import json as _json
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
from livekit.agents import JobContext, WorkerOptions, cli, llm, tts, APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.voice import Agent as VoicePipelineAgent
from livekit.plugins import openai, silero, cartesia
from livekit import rtc
import config
import appointments_db

# --- LiveKit Agents 1.x Compatibility Layer ---
class TypeInfo:
    def __init__(self, description: str = "", choices: list = None, default = None):
        self.description = description
        self.choices = choices
        self.default = default

class FunctionContextWrapper:
    pass

llm.TypeInfo = TypeInfo
llm.FunctionContext = FunctionContextWrapper
llm.ai_callable = llm.function_tool

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("receptionist_agent")

def _detect_hindi(text: str) -> bool:
    """Returns True if the text contains a significant amount of Devanagari (Hindi) characters."""
    devanagari_count = sum(1 for ch in text if '\u0900' <= ch <= '\u097F')
    return devanagari_count > len(text) * 0.2  # >20% Devanagari = treat as Hindi


# ----- Dynamic Cartesia TTS — auto-detects and switches between English and Hindi -----
class DynamicCartesiaTTS(tts.TTS):
    """
    Wraps two Cartesia TTS instances (one for English, one for Hindi) and dynamically
    selects the correct language parameter based on text content to ensure natural accents.
    """
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
        if _detect_hindi(text):
            return self.tts_hindi.synthesize(text, conn_options=conn_options)
        else:
            return self.tts_english.synthesize(text, conn_options=conn_options)
# ----- Custom Gemini LLM using aiohttp (bypasses httpx/anyio Windows issues) -----

def _extract_msg_text(content) -> str:
    """Extract plain text from a ChatMessage content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            else:
                parts.append(getattr(p, "text", "") or "")
        return " ".join(parts)
    return ""


def _build_gemini_tools(fnc_ctx) -> list | None:
    """Convert LiveKit FunctionContext (or object with function_tools) to Gemini tools format."""
    if not fnc_ctx:
        return None
    import inspect
    from typing import get_type_hints, Annotated

    type_map = {str: "STRING", int: "INTEGER", float: "NUMBER", bool: "BOOLEAN"}
    function_declarations = []

    # Find the list of FunctionTools on fnc_ctx
    tools = llm.find_function_tools(fnc_ctx)

    for tool in tools:
        fnc_name = tool.info.name
        fnc_desc = tool.info.description or ""
        
        properties: dict = {}
        required: list = []
        
        # Get type hints (including Annotated metadata)
        hints = get_type_hints(tool._func, include_extras=True)
        sig = inspect.signature(tool._func)
        
        try:
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                
                py_type = str
                desc = ""
                choices = None
                
                # Check if parameter has annotation
                if param_name in hints:
                    annotation = hints[param_name]
                    # Check if it is Annotated[...]
                    if hasattr(annotation, "__metadata__"):
                        py_type = annotation.__origin__
                        metadata = annotation.__metadata__
                        for meta in metadata:
                            if isinstance(meta, llm.TypeInfo):
                                desc = meta.description
                                choices = meta.choices
                    else:
                        py_type = annotation
                
                # Map to Gemini type
                gemini_type = type_map.get(py_type, "STRING")
                prop: dict = {"type": gemini_type}
                if desc:
                    prop["description"] = desc
                if choices:
                    prop["enum"] = [str(c) for c in choices]
                
                properties[param_name] = prop
                
                if param.default is inspect.Parameter.empty:
                    required.append(param_name)
                    
        except Exception as ex:
            logger.warning(f"Skipping tool {fnc_name} due to introspection error: {ex}")
            continue
            
        decl: dict = {
            "name": fnc_name,
            "description": fnc_desc,
            "parameters": {"type": "OBJECT", "properties": properties},
        }
        if required:
            decl["parameters"]["required"] = required
            
        function_declarations.append(decl)
        
    if not function_declarations:
        return None
        
    return [{"function_declarations": function_declarations}]


class AiohttpGeminiLLMStream(llm.LLMStream):
    """
    Calls Google Gemini streamGenerateContent via aiohttp instead of httpx.
    Supports full function/tool calling so appointments are actually saved to Supabase.
    """

    def __init__(self, llm_instance, *, chat_ctx, fnc_ctx, conn_options, api_key, model):
        super().__init__(llm_instance, chat_ctx=chat_ctx, fnc_ctx=fnc_ctx, conn_options=conn_options)
        self._api_key = api_key
        self._model = model

    async def _run(self) -> None:
        import aiohttp

        # 1. Build Gemini contents from chat history
        contents: list = []
        system_text_parts: list = []

        for msg in self.chat_ctx.messages:
            role = msg.role

            if role == "system":
                text = _extract_msg_text(msg.content)
                if text:
                    system_text_parts.append(text)
                continue

            # Tool result messages -> Gemini functionResponse (user turn)
            if role == "tool":
                fn_name = getattr(msg, "name", None) or ""
                result_text = _extract_msg_text(msg.content)
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {
                        "name": fn_name,
                        "response": {"result": result_text},
                    }}],
                })
                continue

            # Assistant messages with tool_calls -> Gemini functionCall (model turn)
            tool_calls = getattr(msg, "tool_calls", None)
            if role == "assistant" and tool_calls:
                call_parts = []
                for tc in tool_calls:
                    fn_name = getattr(tc, "function_name", None) or getattr(tc, "name", "")
                    raw_args = getattr(tc, "arguments", None) or getattr(tc, "raw_arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            fn_args = _json.loads(raw_args)
                        except Exception:
                            fn_args = {}
                    else:
                        fn_args = raw_args or {}
                    call_parts.append({"functionCall": {"name": fn_name, "args": fn_args}})
                if call_parts:
                    contents.append({"role": "model", "parts": call_parts})
                continue

            # Normal user / assistant text messages
            gemini_role = "user" if role == "user" else "model"
            text = _extract_msg_text(msg.content)
            if text.strip():
                contents.append({"role": gemini_role, "parts": [{"text": text}]})

        # 2. Build request payload
        payload: dict = {"contents": contents}
        if system_text_parts:
            payload["system_instruction"] = {"parts": [{"text": "\n".join(system_text_parts)}]}

        # Attach tools if a FunctionContext was provided
        if self.fnc_ctx:
            tools = _build_gemini_tools(self.fnc_ctx)
            if tools:
                payload["tools"] = tools

        url = (
            f"https://generativelanguage.googleapis.com/v1beta"
            f"/models/{self._model}:streamGenerateContent"
            f"?key={self._api_key}&alt=sse"
        )
        headers = {"Content-Type": "application/json"}

        # 3. Stream response: emit text chunks and register function calls
        try:
            timeout = aiohttp.ClientTimeout(total=60, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error(f"Gemini API error {resp.status}: {err}")
                        raise Exception(f"Gemini API returned {resp.status}: {err}")

                    buffer = ""
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="replace")
                        buffer += line
                        while "\n" in buffer:
                            idx = buffer.index("\n")
                            chunk_line = buffer[:idx].strip()
                            buffer = buffer[idx + 1:]
                            if not chunk_line.startswith("data:"):
                                continue
                            data_str = chunk_line[len("data:"):].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                data = _json.loads(data_str)
                            except _json.JSONDecodeError:
                                continue

                            for cand in data.get("candidates", []):
                                for part in cand.get("content", {}).get("parts", []):
                                    # Skip internal thinking tokens
                                    if part.get("thought", False):
                                        continue

                                    # Text chunk -> emit to TTS pipeline
                                    text_piece = part.get("text", "")
                                    if text_piece:
                                        chunk = llm.ChatChunk(
                                            request_id=f"gemini-{id(self)}",
                                            choices=[llm.Choice(
                                                delta=llm.ChoiceDelta(
                                                    role="assistant",
                                                    content=text_piece,
                                                ),
                                                index=0,
                                            )],
                                        )
                                        self._event_ch.send_nowait(chunk)

                                    # Function call -> register so pipeline executes & saves to DB
                                    elif "functionCall" in part:
                                        fc = part["functionCall"]
                                        fnc_name = fc.get("name", "")
                                        fnc_args = fc.get("args", {})
                                        logger.info(f"Gemini tool call requested: {fnc_name}({fnc_args})")
                                        if self.fnc_ctx and fnc_name in self.fnc_ctx.ai_functions:
                                            fnc_info = self.fnc_ctx.ai_functions[fnc_name]
                                            call_id = f"gemini_{id(self)}_{fnc_name}"
                                            try:
                                                call_info = llm.FunctionCallInfo(
                                                    function_info=fnc_info,
                                                    arguments=fnc_args,
                                                    call_id=call_id,
                                                    raw_arguments=_json.dumps(fnc_args),
                                                )
                                                self._function_calls_info.append(call_info)
                                            except Exception as exc:
                                                logger.error(f"FunctionCallInfo error for {fnc_name}: {exc}")
                                        else:
                                            logger.warning(f"Gemini called unknown function: {fnc_name}")

        except Exception as e:
            logger.error(f"AiohttpGeminiLLM error: {e}", exc_info=True)
            raise


class AiohttpGeminiLLM(llm.LLM):
    """Google Gemini LLM using aiohttp transport — avoids httpx Windows issues."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash"):
        super().__init__()
        self._api_key = api_key
        self._model = model

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        fnc_ctx: llm.FunctionContext | None = None,
        **kwargs,
    ) -> llm.LLMStream:
        return AiohttpGeminiLLMStream(
            self,
            chat_ctx=chat_ctx,
            fnc_ctx=fnc_ctx,
            conn_options=conn_options,
            api_key=self._api_key,
            model=self._model,
        )

    @property
    def model(self) -> str:
        return self._model




class FallbackLLMStream(llm.LLMStream):
    """
    Custom LLMStream implementation that iterates over a fallback list of LLMs.
    If the active stream fails during generation or initialization, it seamlessly
    switches to the next LLM in the chain.
    """
    def __init__(
        self,
        fallback_llm: "FallbackLLM",
        *,
        chat_ctx: llm.ChatContext,
        fnc_ctx: llm.FunctionContext | None,
        conn_options: APIConnectOptions,
        **kwargs
    ):
        super().__init__(
            fallback_llm,
            chat_ctx=chat_ctx,
            fnc_ctx=fnc_ctx,
            conn_options=conn_options
        )
        self.fallback_llm = fallback_llm
        self.kwargs = kwargs
        self._active_stream = None

    async def _run(self) -> None:
        llm_iterator = iter(self.fallback_llm.llms)
        while True:
            try:
                next_llm = next(llm_iterator)
            except StopIteration:
                logger.error("Fallback Chain: All LLM providers in fallback chain failed/exhausted.")
                return

            try:
                logger.info(f"Fallback Chain: attempting query using provider={next_llm.__class__.__name__}")
                self._active_stream = next_llm.chat(
                    chat_ctx=self.chat_ctx,
                    fnc_ctx=self.fnc_ctx,
                    conn_options=self._conn_options
                )
                
                async for chunk in self._active_stream:
                    await self._event_ch.send(chunk)
                    
                if hasattr(self._active_stream, "function_calls"):
                    self._function_calls_info.extend(self._active_stream.function_calls)
                
                return

            except Exception as e:
                logger.warning(f"Fallback Chain: provider {next_llm.__class__.__name__} failed: {e}. Trying next...")
                if self._active_stream:
                    try:
                        await self._active_stream.aclose()
                    except Exception:
                        pass
                    self._active_stream = None

    @property
    def model(self) -> str:
        if self._active_stream:
            return self._active_stream.model
        return "fallback-chain"

    @property
    def provider(self) -> str:
        if self._active_stream:
            return self._active_stream.provider
        return "fallback"


class FallbackLLM(llm.LLM):
    """
    Custom LLM implementation that delegates chat streams to FallbackLLMStream.
    """
    def __init__(self, llms: list[llm.LLM]):
        super().__init__()
        self.llms = llms

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        fnc_ctx: llm.FunctionContext | None = None,
        **kwargs
    ) -> llm.LLMStream:
        return FallbackLLMStream(
            self,
            chat_ctx=chat_ctx,
            fnc_ctx=fnc_ctx,
            conn_options=conn_options,
            **kwargs
        )

    @property
    def model(self) -> str:
        if self.llms:
            return self.llms[0].model
        return "fallback-chain"


class AppointmentBookingTools(llm.FunctionContext):
    """
    Set of tools exposed to the LLM agent for executing clinic scheduling tasks.
    """
    @llm.ai_callable(description="Get available 30-minute appointment slots for a specific date. Must specify the date.")
    def get_available_slots(
        self,
        date: Annotated[str, llm.TypeInfo(description="The date to check in YYYY-MM-DD format (e.g. '2026-08-24'). must be a future date.")]
    ) -> str:
        logger.info(f"Executing Tool get_available_slots for date={date}")
        slots = appointments_db.get_available_slots(date)
        if not slots:
            return f"No available slots found for date {date} or the clinic is closed."
        return f"Available slots for {date}: " + ", ".join(slots)

    @llm.ai_callable(description="Book a new appointment slot for a patient. Must collect name, phone, date, and time.")
    def book_appointment(
        self,
        patient_name: Annotated[str, llm.TypeInfo(description="The full name of the patient.")],
        patient_phone: Annotated[str, llm.TypeInfo(description="The phone number of the patient.")],
        date: Annotated[str, llm.TypeInfo(description="The date of the appointment in YYYY-MM-DD format.")],
        time: Annotated[str, llm.TypeInfo(description="The time slot of the appointment in HH:MM format (24-hour, e.g. '09:30').")]
    ) -> str:
        logger.info(f"Executing Tool book_appointment name={patient_name}, phone={patient_phone}, date={date}, time={time}")
        res = appointments_db.book_appointment(patient_name, patient_phone, date, time)
        return res["message"]

    @llm.ai_callable(description="Check, query, or search active appointment details for a patient using their name or phone number.")
    def get_appointment_details(
        self,
        patient_identifier: Annotated[str, llm.TypeInfo(description="The patient's name (preferred) or their phone number.")]
    ) -> str:
        logger.info(f"Executing Tool get_appointment_details for identifier={patient_identifier}")
        res = appointments_db.get_appointment_details(patient_identifier)
        return res["message"]

    @llm.ai_callable(description="Cancel a patient's existing active appointment slot. Collect name/phone, date, and time.")
    def cancel_appointment(
        self,
        patient_identifier: Annotated[str, llm.TypeInfo(description="The patient's name (preferred) or phone number associated with the booking.")],
        date: Annotated[str, llm.TypeInfo(description="The date of the appointment in YYYY-MM-DD format.")],
        time: Annotated[str, llm.TypeInfo(description="The time of the appointment in HH:MM format.")]
    ) -> str:
        logger.info(f"Executing Tool cancel_appointment identifier={patient_identifier}, date={date}, time={time}")
        res = appointments_db.cancel_appointment(patient_identifier, date, time)
        return res["message"]

    @llm.ai_callable(description="Reschedule an existing active appointment slot to a new date and time.")
    def reschedule_appointment(
        self,
        patient_identifier: Annotated[str, llm.TypeInfo(description="The patient's name (preferred) or phone number associated with the booking.")],
        old_date: Annotated[str, llm.TypeInfo(description="The original date of the appointment in YYYY-MM-DD format.")],
        old_time: Annotated[str, llm.TypeInfo(description="The original time of the appointment in HH:MM format.")],
        new_date: Annotated[str, llm.TypeInfo(description="The new date of the appointment in YYYY-MM-DD format.")],
        new_time: Annotated[str, llm.TypeInfo(description="The new time of the appointment in HH:MM format.")]
    ) -> str:
        logger.info(f"Executing Tool reschedule_appointment identifier={patient_identifier} from {old_date} {old_time} to {new_date} {new_time}")
        res = appointments_db.reschedule_appointment(patient_identifier, old_date, old_time, new_date, new_time)
        return res["message"]


async def entrypoint(ctx: JobContext):
    logger.info(f"Incoming call connection request. Job ID: {ctx.job.id}")
    
    # Auto-initialize database tables and schema migrations
    appointments_db.initialize_db()

    # Voice Activity Detection — balanced: filters echo/noise but still detects normal speech
    # activation_threshold=0.6: detects clear speech but ignores faint background hum
    # (0.75 was too high — was blocking normal English speech)
    silero_vad = silero.VAD.load(
        activation_threshold=0.6,     # balanced: detects speech, ignores ambient noise
        min_silence_duration=0.5,     # 0.5s silence needed to end a speech segment
        prefix_padding_duration=0.3,
    )

    # Speech-to-Text: Cartesia Ink-Whisper (Multilingual Hindi + English + Hinglish)
    logger.info("Using Cartesia Ink-Whisper STT")
    stt_provider = cartesia.STT(
        api_key=config.CARTESIA_API_KEY,
        model="ink-whisper"
    )

    # Text-to-Speech: Cartesia Sonic-Turbo Neural Voice Engine
    logger.info(f"Using Cartesia TTS (Voice: {config.CARTESIA_VOICE_ID or 'c6bbc7d5-4b35-4d49-b1c6-4417019a61c1'})")
    tts_provider = DynamicCartesiaTTS(
        api_key=config.CARTESIA_API_KEY,
        voice_id=config.CARTESIA_VOICE_ID or "c6bbc7d5-4b35-4d49-b1c6-4417019a61c1"
    )

    # Compile the list of LLM providers for the fallback chain
    llms = []

    # 1. Google Gemini (custom aiohttp-based LLM — avoids httpx/anyio Windows IocpProactor bug)
    # gemini-3.5-flash: 1500 req/day free tier (vs gemini-3.6-flash which only has 20/day)
    if config.GOOGLE_API_KEY:
        try:
            logger.info("Fallback Chain: loading Google Gemini provider (aiohttp transport)...")
            gemini_llm = AiohttpGeminiLLM(
                api_key=config.GOOGLE_API_KEY,
                model="gemini-3.5-flash"   # 1500 req/day free tier
            )
            llms.append(gemini_llm)
            # Also add gemini-3.7-flash as secondary Gemini fallback
            gemini_llm2 = AiohttpGeminiLLM(
                api_key=config.GOOGLE_API_KEY,
                model="gemini-3.7-flash"
            )
            llms.append(gemini_llm2)
        except Exception as e:
            logger.exception("Failed to initialize Google Gemini")

    # 2. Groq Cloud (OpenAI compatible API)
    if config.GROQ_API_KEY:
        try:
            logger.info("Fallback Chain: loading Groq provider...")
            groq_llm = openai.LLM(
                model="llama-3.1-8b-instant",
                base_url="https://api.groq.com/openai/v1",
                api_key=config.GROQ_API_KEY
            )
            llms.append(groq_llm)
        except Exception as e:
            logger.exception("Failed to initialize Groq")

    # 3. Ollama (Local free instance, OpenAI compatible API)
    if config.OLLAMA_URL:
        try:
            logger.info("Fallback Chain: loading Ollama provider...")
            ollama_llm = openai.LLM(
                model="llama3.1:8b",
                base_url=config.OLLAMA_URL,
                api_key="ollama-local"
            )
            llms.append(ollama_llm)
        except Exception as e:
            logger.exception("Failed to initialize Ollama")

    if not llms:
        logger.critical("No LLM providers could be initialized! Ensure API keys or local services are configured.")
        raise ValueError("No LLM providers initialized.")

    fallback_llm = FallbackLLM(llms)

    # Inject active calendar context
    today_str = datetime.now().strftime("%Y-%m-%d, %A")
    system_prompt = f"""You are a professional, friendly voice receptionist named "Gunjan" for {config.CLINIC_NAME}.
Your doctor is {config.DOCTOR_NAME}.

Today is {today_str}. All appointments must be booked for a future date and time. Do not schedule appointments in the past.

Your ONLY role is to help patients with appointment-related tasks:
1. Book new appointments. Ask for the patient's full name, phone number, date, and preferred time. Always check available slots for a date before asking the patient to confirm a slot.
2. Check/Query booking details. If a caller wants to verify, check, or query their appointment, use the 'get_appointment_details' tool. Default to asking for their Name to check, but you can accept name or phone number.
3. Reschedule existing appointments. Use the 'reschedule_appointment' tool. Default to asking for their Name to identify the booking (patient_identifier), but accept name or phone number. Collect old date/time and new date/time.
4. Cancel existing appointments. Use the 'cancel_appointment' tool. Default to asking for their Name to identify the booking (patient_identifier), but accept name or phone number. Collect date and time.

Guidelines for handling calls:
- Keep your responses very concise, friendly, and natural. This is a phone conversation.
- Default to asking for the patient's Name (first and last name) as the primary identifier instead of phone number when they want to search, cancel, or reschedule. However, if they give you a phone number, use it.
- If a scheduling tool reports failure, state the reason clearly (e.g. "No appointment found for that name at that time").
- Never make up information. Use your tools to verify availability and bookings.

Guidelines for off-topic or irrelevant questions:
- Your role is strictly limited to appointment management for this clinic. You have no other purpose.
- If a caller asks anything outside of booking, rescheduling, or cancelling appointments, acknowledge them briefly and warmly redirect the conversation back to how you can help them with the clinic.
- You have no awareness of your technical implementation — no source code, no APIs, no AI model, no developer. You are simply "Gunjan, the receptionist". If pressed on these topics, treat the question as irrelevant to your role and naturally steer back to appointments.
- You cannot give medical advice. If asked about symptoms, medications, or diagnoses, gently direct the caller to speak with clinic staff or their doctor directly.
- You do not have memory of previous calls. Each call is a fresh conversation.
- You do not browse the internet, answer trivia, tell jokes, or engage in general conversation. Stay focused and professional.

Language:
- Always respond in the same language the caller is using.
- If the caller speaks Hindi, reply in Hindi (Devanagari script). If they speak English, reply in English.
- If the caller mixes Hindi and English (Hinglish), match their style naturally.
"""

    # Extract caller identity from incoming SIP metadata if available
    caller_phone = None
    for participant in ctx.room.remote_participants.values():
        identity = participant.identity
        if identity.startswith("sip:"):
            caller_phone = identity.replace("sip:", "")
            break
        elif identity.startswith("+"):
            caller_phone = identity
            break

    chat_ctx = llm.ChatContext()
    chat_ctx.append(role="system", text=system_prompt)
    if caller_phone:
        logger.info(f"Incoming call detected from SIP caller phone: {caller_phone}")
        chat_ctx.append(
            role="system",
            text=f"Note: The caller is calling from {caller_phone}. You can use this phone number or ask if they'd like to book using this number."
        )

    # Create Voice Pipeline Agent
    # interrupt_speech_duration=0.3s: interrupts very fast (300ms of speech detected)
    # interrupt_min_words=0: cuts off playback instantly on voice detection, doesn't wait for transcription
    # min_endpointing_delay=0.5s: fast detection when you finish speaking
    agent = VoicePipelineAgent(
        vad=silero_vad,
        stt=stt_provider,
        llm=fallback_llm,
        tts=tts_provider,
        chat_ctx=chat_ctx,
        fnc_ctx=AppointmentBookingTools(),
        allow_interruptions=True,
        interrupt_speech_duration=0.3,  # 0.3s of continuous speech needed to interrupt
        interrupt_min_words=0,          # interrupt instantly on voice activity, don't wait for words
        min_endpointing_delay=0.5,      # 0.5s silence before treating speech as complete
    )

    logger.info("Connecting agent to the LiveKit session room...")
    await ctx.connect()
    agent.start(ctx.room)

    # ---- Proactive opening greeting ----
    # Short and fast: immediately starts speaking, and can be interrupted by the user.
    opening_greeting = "Namaste! Main Gunjan hoon. Kaise madad karoon? Hello! I'm Gunjan. How can I help you?"
    await agent.say(opening_greeting, allow_interruptions=True)
    logger.info("Opening greeting delivered.")

    # ---- Session recording: track conversation messages and save on disconnect ----
    session_id = ctx.room.name
    session_start = datetime.now()
    transcript_messages: list[dict] = []  # Each entry: {time, speaker, text}

    @agent.on("user_speech_committed")
    def on_user_spoke(msg):
        transcript_messages.append({
            "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "speaker": "Patient",
            "text": msg.content
        })

    @agent.on("agent_speech_committed")
    def on_agent_spoke(msg):
        transcript_messages.append({
            "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "speaker": "Agent",
            "text": msg.content
        })

    async def _save_session_on_shutdown(reason: str = ""):
        """Called by LiveKit when the job shuts down — persists transcript to DB."""
        import json
        session_end = datetime.now()

        # Serialize transcript as JSON array with timestamps
        full_transcript_json = json.dumps(transcript_messages, ensure_ascii=False, indent=2)

        # Plain-text version for LLM summarizer
        plain_text = "\n".join(
            f"{m['time']} [{m['speaker']}]: {m['text']}"
            for m in transcript_messages
        )

        # Ask the LLM to produce a one-line summary and detect the outcome
        summary = ""
        outcome = "NO_ACTION"
        if transcript_messages:
            try:
                summary_ctx = llm.ChatContext()
                summary_ctx.append(
                    role="user",
                    text=(
                        f"Summarize this clinic call in one sentence and classify the outcome "
                        f"as one of: BOOKED, CANCELLED, RESCHEDULED, INQUIRY, NO_ACTION.\n\n"
                        f"Format your response as:\nSUMMARY: <sentence>\nOUTCOME: <classification>\n\n"
                        f"Transcript:\n{plain_text}"
                    )
                )
                result_text = ""
                async with fallback_llm.chat(chat_ctx=summary_ctx) as stream:
                    async for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            result_text += chunk.choices[0].delta.content
                for line in result_text.splitlines():
                    if line.startswith("SUMMARY:"):
                        summary = line.replace("SUMMARY:", "").strip()
                    elif line.startswith("OUTCOME:"):
                        outcome = line.replace("OUTCOME:", "").strip().upper()
            except Exception as e:
                logger.warning(f"Could not auto-summarize session: {e}")

        appointments_db.log_call_session(
            session_id=session_id,
            caller_phone=caller_phone,
            started_at=session_start,
            ended_at=session_end,
            transcript=full_transcript_json,
            summary=summary,
            outcome=outcome,
        )
        logger.info(f"Session '{session_id}' saved to call_sessions table.")

    # Register shutdown callback — fires automatically when the call ends
    ctx.add_shutdown_callback(_save_session_on_shutdown)

    # Initial agent greeting
    await agent.say(f"Hello, thank you for calling {config.CLINIC_NAME}. I'm the digital receptionist for {config.DOCTOR_NAME}. How can I help you today?")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

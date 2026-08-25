# Clinigo: Low-Latency AI Voice Receptionist & Clinic Operations Dashboard

Clinigo is a production-grade, low-latency AI receptionist agent built to handle incoming clinic phone calls via Tata Smartflo, answer queries, verify slot availability, and book appointments sequentially into a PostgreSQL database. It features a dark-themed management dashboard to monitor real-time KPIs, inspect call transcripts, download structured JSON call logs, and visualize calendar capacity.

---

## 🏗️ Architecture Overview

```mermaid
graph TD
    A[Caller / Phone Client] <-->|GSM Audio / SIP Trunk| B(Tata Smartflo Telephony)
    B <-->|WebSocket Stream 8kHz PCMU| C[Tata Voice Server :8080]
    C -->|Upsampled 16kHz PCM| D(Cartesia STT : Ink-Whisper)
    C <-->|Function Calling / Context| E(Groq LPU : openai/gpt-oss-120b)
    E <-->|Queries & Updates| F[(PostgreSQL Database)]
    C <-->|Low-Latency Audio Generation| G(Cartesia TTS : sonic-turbo)
    H[Clinic Operations Dashboard :5000] <-->|Reads/Writes| F
```

---

## ✨ Key Features

*   **⚡ Sub-200ms Voice Synthesis**: Utilizes Cartesia's `sonic-turbo` model and custom fade-out curves to eliminate murmurs and deliver fluid audio.
*   **🎙️ Telephony-Optimized VAD**: Uses a tuned Silero Voice Activity Detector (600ms silence threshold, 0.70 activation confidence) to filter out GSM packet jitter and noise.
*   **🤖 Multi-Turn Groq LPU Integration**: Leverages `openai/gpt-oss-120b` for rapid (under 150ms) system reasoning, slots filtering, and transaction logic.
*   **📞 Automated Sequential Call Flow**:
    1.  **Step 1**: Check & Offer Slots
    2.  **Step 2**: Collect Patient Name
    3.  **Step 3**: Collect 10-Digit Mobile Number
    4.  **Step 4**: Confirm and Book
*   **🔄 Auto-Healing Postgres Connections**: Automatically detects stale or closed pool connections, reconnecting instantly to prevent call drop-offs.
*   **💾 Structured JSON Logs**: Serializes and stores turn-by-turn conversational histories inside the database, ready to be reviewed, copied, or downloaded from the dashboard.
*   **📊 Live Operations Dashboard**: Built using glassmorphic UI principles, showcasing conversion rates, project revenue metrics, and interactive slot capacity blocks.

---

## 🛠️ Tech Stack

*   **Speech-to-Text**: Cartesia Ink-Whisper (`ink-whisper`)
*   **Text-to-Speech**: Cartesia Sonic-Turbo (`sonic-turbo`)
*   **LLM Platform**: Groq LPU API (`openai/gpt-oss-120b` / `openai/gpt-oss-20b`)
*   **Database**: PostgreSQL (Supabase / Neon)
*   **Telephony Integrator**: Tata Smartflo (WebSocket audio channel)
*   **Dashboard**: Flask + Vanilla CSS (Glassmorphic) + SVG Assets

---

## 🚀 Setup & Installation

### 1. Prerequisites
*   Python 3.10+
*   PostgreSQL Database URL
*   Active Cartesia, Groq, and Livekit API accounts

### 2. Dependency Setup
Install required packages using:
```bash
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file in the root directory based on the following pattern:
```env
LIVEKIT_URL=your_livekit_url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

GOOGLE_API_KEY=your_google_api_key
GROQ_API_KEY=your_groq_api_key
CARTESIA_API_KEY=your_cartesia_api_key
CARTESIA_VOICE_ID=c6bbc7d5-4b35-4d49-b1c6-4417019a61c1

DATABASE_URL=postgresql://user:password@host:port/dbname
```

### 4. Database Initialization
Ensure your schema is ready by running:
```bash
python manage_db.py
```

---

## ⚙️ Running the Application

### Start Voice Server (Port 8080)
The voice server coordinates all incoming Tata Smartflo connections:
```bash
python tata_voice_server.py
```

### Start Operations Dashboard (Port 5000)
Run the monitoring frontend:
```bash
python dashboard.py
```

---

## 📈 Dashboard Features
Access `http://localhost:5000` to manage clinic operations:
*   **KPI Grid**: Track total bookings, projected revenue, conversion rate, and call count.
*   **Slot Matrix**: Interactive display of morning and afternoon slot availability.
*   **Call Logs**: Review details of completed sessions, copy full JSON logs to the clipboard, or download them as files.

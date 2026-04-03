# Avatar Server

FastAPI backend for a distributed AI companion system. Connects a cloud LLM, TTS engine with emotion control, and VRM avatar display into a unified pipeline. Character identity is driven by `character.json` — no hardcoded character names in source code.

## Architecture

```
[S25 Edge] --Telegram--> [Ubuntu Server] --WebSocket--> [Tab A9+]
   |                      (Bot + AI Brain)               (VRM Display)
   |                      :8800
   |                           |
   +--CompanionSensor-->  [RTX 4070 PC]
   (steps + screen time)  (TTS: CosyVoice 3)
                          :9091
```

**Chat flow:**
1. User sends message via Telegram
2. Avatar server queries Claude CLI (or Groq/Cerebras fallback) with personality + memories + tools
3. LLM response includes emotion tag: `[HAPPY] That sounds fun!`
4. If JP mode: bilingual response parsed (`en:` subtitle, `jp:` TTS)
5. Text sent to CosyVoice 3 with emotion instruction → WAV audio
6. Response + emotion + audio broadcast via WebSocket to Unity avatar
7. Unity app plays audio with lip-sync and emotion-appropriate expressions

## Features

### Core
- **Cloud LLM** — Claude CLI (primary, Sonnet) → Groq → Cerebras fallback chain
- **Emotion System** — `[HAPPY]`, `[SAD]`, `[SURPRISED]`, `[ANGRY]`, `[THINKING]`, `[NEUTRAL]`
- **TTS with Emotion** — CosyVoice 3 (primary, voice clone + emotion) with Kokoro-ONNX fallback
- **Bilingual JP/EN** — `/language` toggle, LLM responds in both, TTS speaks selected language
- **Speech-to-Text** — faster-whisper transcription
- **WebSocket Relay** — persistent connection to VRM avatar client
- **Conversation Memory** — 3-tier (recent history, persistent SQLite, core memories + session summaries)
- **Character-Agnostic** — all identity from `character.json` (name, nicknames, feed keywords)

### Plugin System
Modular feature system — each plugin is a folder with manifest, handler, storage, and prompt docs.

| Plugin | Tools | /p.* Commands | Background |
|--------|-------|---------------|------------|
| todo | add_task, list_tasks, complete_task | tasks, tasks.add, tasks.done | check (reminders) |
| money | add_expense, add_income, check_balance | balance, expenses, balance.budget | check (spending nags) |
| calorie | add_meal, check_calories | calories, meals, calories.target | — |
| weather | check_weather | weather | — |
| calendar | check_calendar | calendar | — |
| anime | find_anime | anime | — |
| meme | get_political_meme | meme | — |
| prayer | — | prayer | task (run_during_sleep) |
| rss | — | rss | task (character posts) |
| clockin | — | clockin, clockin.status | — (disabled) |

### Main Features (Unity-connected)
- **Costume System** — swap VRM models via WebSocket
- **Gacha / Roulette / THR** — entertainment animations on tablet
- **Touch Interaction** — poke avatar, LLM responds based on mood
- **Sleep Mode** — auto-sleep 00:00-05:00 WIB, reduced activity
- **Mood System** — 0-100, decays over time, affects personality
- **Sticker System** — 24 stickers matched by emotion + keywords
- **Sensor** — screen time + step tracking from Android app, step goal at 18:00

### Background Services
- Idle talk (4 categories: smart 20%, tool-based 30%, random 30%, sensor 20-50%)
- Prayer time reminders (plugin, sleep-aware)
- RSS feed monitoring (plugin, character post detection)
- Calendar reminders (Nextcloud CalDAV)
- Spending threshold nags
- Step goal check at 18:00 WIB
- Session auto-summarization
- Scheduled plugin actions (cron-like)

## Prerequisites

- **Python 3.11+**
- **Claude CLI** (primary LLM) or any OpenAI-compatible provider
- **CosyVoice 3** (TTS with emotion) or Kokoro-ONNX (fallback)
- **VRM avatar client** (optional — server works standalone)

## Setup

```bash
# Clone and create venv
git clone https://github.com/abdulyasir100/vrm-avatar-server.git avatar-server
cd avatar-server
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your values

# Run
python -m uvicorn main:app --host 0.0.0.0 --port 8800
```

## Configuration

All config via environment variables (`.env`). See `.env.example` for full list.

Key variables:
| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `claude` | LLM provider: `claude`, `groq`, `cerebras`, `lmstudio` |
| `TTS_ENGINE` | `cosyvoice` | TTS backend: `cosyvoice`, `qwen3tts`, `kokoro` |
| `COSYVOICE_API_URL` | `http://100.83.33.113:9091` | CosyVoice 3 server URL |
| `CHARACTER_CARD_PATH` | `character.json` | Character identity file |

Character identity (`character.json`):
```json
{
  "name": "Your Character Name",
  "nickname": ["nick1", "nick2"],
  "feed_keywords": ["keyword1", "keyword2"],
  "tags": ["tag1", "tag2"]
}
```

## Project Structure

```
avatar-server/
├── main.py                  # FastAPI app entry
├── config.py                # Configuration + character loading
├── character.json           # Character identity (name, nicknames, tags)
├── prompts/
│   ├── personality.md       # Character personality card
│   └── system.md            # System rules (response format, emotion tags)
├── plugins/                 # Modular plugins (see Plugin System)
│   ├── todo/                # Task management
│   ├── money/               # Expense/income tracking
│   ├── calorie/             # Calorie tracking + food DB
│   ├── weather/             # Weather forecast
│   ├── calendar/            # CalDAV calendar
│   ├── anime/               # Anime streaming search
│   ├── meme/                # Political memes
│   ├── prayer/              # Prayer time reminders
│   ├── rss/                 # RSS feed monitoring
│   ├── clockin/             # Work clock-in (disabled)
│   └── plugin-example/      # Template for new plugins
├── routers/                 # API endpoints
├── services/                # Core services (LLM, TTS, memory, mood, etc.)
│   └── tools/               # Main feature tools (costume, gacha, memory, sensor)
├── data/                    # Runtime data (gitignored)
│   ├── memory.db            # SQLite (conversations, memories, mood, sensor)
│   └── plugins/             # Plugin SQLite databases
├── audio/                   # Generated WAV files (gitignored)
└── tests/
    └── test_plugin_system.py # Comprehensive blackbox test (16 sections)
```

## Plugin System

Plugins are modular features in `plugins/`. Each plugin has:
- `manifest.json` — tools, triggers, commands, settings, background config
- `handler.py` — tool handlers + Telegram command handlers
- `storage.py` — SQLite storage (optional)
- `idle.py` — idle talk prompts (optional)
- `prompt.md` — tool docs injected into LLM prompt

Copy `plugins/plugin-example/` to create new plugins.

### Intent Prefix
Plugin tools require intent words to trigger (avoids false positives):
- Character nicknames, "add", "check", "show", "remind", "spent", "ate", etc.
- Main features (costume, gacha, memory) trigger without prefix.

### Telegram Commands
Dynamic from plugin manifests: `/p.tasks`, `/p.balance`, `/p.calories`, `/p.weather`, etc.
Inline keyboard buttons for CRUD (done/delete tasks, delete expenses).

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | LLM chat + TTS + tool execution |
| `POST` | `/tts` | TTS only |
| `POST` | `/transcribe` | Speech-to-text |
| `WS` | `/ws` | WebSocket to avatar client |
| `POST` | `/sensor` | Receive sensor data (screen time, steps) |
| `GET/POST` | `/sensor/step-goal` | Step goal management |
| `GET` | `/plugin/list` | List all plugins + commands |
| `POST` | `/plugin/command` | Execute plugin Telegram command |
| `POST` | `/plugin/callback` | Handle inline button callbacks |
| `GET` | `/plugin/guide` | Auto-generated tool guide |
| `GET` | `/status` | Health check |
| `GET/POST` | `/admin/config` | Runtime config |

## TTS Engine

**CosyVoice 3** (primary) — voice clone with emotion control:
- EN: Chinese emotion instructions (the model was trained this way)
- JP: Volume/style instructions (loud=angry, soft=sad)
- Bilingual mode: `/language jp` for Japanese voice with English subtitles

**Kokoro-ONNX** (fallback) — CPU, generic voice, no emotion.

## License

MIT

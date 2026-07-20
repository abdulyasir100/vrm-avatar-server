# Avatar Server

FastAPI backend for an AI companion system. It connects a cloud (or local) LLM, a
TTS engine with emotion control, and a VRM avatar display into one pipeline. The
character is **fully data-driven** — drop in your own `character.json` and it becomes
your companion, with no character names hardcoded in the source.

## How it works

```
[Chat client] ──▶ [Avatar Server] ──WebSocket──▶ [VRM avatar client]
  (Telegram,      (LLM + TTS + memory                (Unity, optional)
   HTTP, …)        + plugins, :8800)
                        │
                   [TTS engine]
```

**Chat flow:**
1. A message arrives (Telegram bot, HTTP `POST /chat`, etc.).
2. The server builds a prompt from the character card + memories + available tools and queries the LLM.
3. The reply starts with an emotion tag: `[HAPPY] That sounds fun!`
4. The text is sent to the TTS engine (with an emotion instruction) → audio.
5. Reply + emotion + audio are broadcast over WebSocket to the avatar client, which plays it with lip-sync and matching expressions.

## Features

- **Pluggable LLM** — Claude CLI, or any OpenAI-compatible provider (Groq, Cerebras, OpenAI, Mistral, LM Studio, Ollama), with a fallback chain.
- **Emotion system** — `[HAPPY]`, `[SAD]`, `[SURPRISED]`, `[ANGRY]`, `[THINKING]`, `[NEUTRAL]`.
- **TTS with emotion** — OmniVoice / CosyVoice (voice clone + emotion) with a Kokoro-ONNX CPU fallback.
- **Bilingual** — `/language` toggle; the LLM can reply in two languages, TTS speaks the selected one.
- **Speech-to-text** — faster-whisper transcription.
- **WebSocket relay** — persistent connection to a VRM avatar client.
- **Conversation memory** — recent history + persistent SQLite + core memories & session summaries.
- **Mood system** — 0–100, decays over time, shapes the character's tone.
- **Character-agnostic** — name, nicknames, owner, timezone, language, and songs all come from `character.json`.
- **Plugin system** — modular features you can add or delete freely (see below).

## Quick start

```bash
git clone https://github.com/abdulyasir100/vrm-avatar-server.git avatar-server
cd avatar-server
python -m venv .venv
source .venv/bin/activate            # .venv\Scripts\activate on Windows
pip install -r requirements.txt

cp character.example.json character.json   # then edit to make it your character
cp .env.example .env                       # then edit your provider keys / hosts

python -m uvicorn main:app --host 0.0.0.0 --port 8800
```

Or with Docker: `docker compose up -d --build` (see `docker-compose.yml`).

Check it's alive: `curl http://localhost:8800/status`.

## Configuration

Everything is set via `.env` (see `.env.example`) or `character.json`. Key knobs:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `claude` | `claude`, `groq`, `cerebras`, `openai`, `mistral`, `lmstudio`, `ollama` |
| `TTS_ENGINE` | `omnivoice` | `omnivoice`, `cosyvoice`, `qwen3tts`, `kokoro` |
| `OMNIVOICE_API_URL` / `COSYVOICE_API_URL` | `http://localhost:919x` | Your TTS server URL |
| `TIMEZONE` / `TZ_OFFSET_HOURS` / `TZ_LABEL` | — | IANA name (e.g. `Asia/Jakarta`) or a fixed offset + optional label |
| `LANGUAGE` / `LOCALE_COUNTRY` | `en` / — | Default language and country |
| `OWNER_NAME` / `OWNER_PRONOUN` | `the user` / `they` | Who the character serves |
| `CHARACTER_CARD_PATH` | `character.json` | Character identity file |

The character card also carries `owner`, `timezone`, `tz_label`, `language`, `country`,
`songs`, and `song_aliases` — env vars win when both are set. See `character.example.json`.

## Plugin system

Plugins are self-contained folders in `plugins/`. The engine scans the folder at
startup — **there are no hardcoded plugin names in the core**, so you can add a plugin
by dropping a folder in, or delete any plugin folder you don't want and the server
still runs.

Each plugin has:
- `manifest.json` — tools, triggers, `/p.*` commands, settings, background config
- `handler.py` — tool handlers (`handle_*`) + Telegram command handlers (`cmd_*`)
- `storage.py` — optional SQLite (stored in `data/plugins/<name>/`)
- `idle.py` — optional idle-talk prompts
- `prompt.md` — tool docs injected into the LLM prompt

Copy `plugins/plugin-example/` to build a new one.

This repo ships the framework plus a few demo plugins — **`weather`, `todo`, `calorie`**
— and the `plugin-example` template. More plugins are available from the plugin
marketplace (browse, download the folder, drop it into `plugins/`, restart).

### Main features (Unity-connected)

Costume swap, gacha/roulette/THR animations, touch interaction, sticker matching, and
sleep mode live in `services/tools/` (they need the WebSocket link to the avatar) and
are always on — they are not plugins.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | LLM chat + TTS + tool execution |
| `POST` | `/tts` | TTS only |
| `POST` | `/transcribe` | Speech-to-text |
| `WS` | `/ws` | WebSocket to the avatar client |
| `GET` | `/plugin/list` | List loaded plugins + commands |
| `POST` | `/plugin/command` | Run a plugin Telegram command |
| `POST` | `/plugin/callback` | Handle inline-button callbacks |
| `GET` | `/status` | Health check |

## Project structure

```
avatar-server/
├── main.py                 # FastAPI app entry
├── config.py               # Configuration + character loading
├── character.json          # Character identity (name, owner, timezone, songs, …)
├── prompts/
│   ├── personality.md      # Fallback persona (used only if character.json is absent)
│   └── system.md           # System rules (response format, emotion tags) — tokenized
├── utils/time.py           # Timezone helper (single source of local time)
├── plugins/                # Modular plugins — delete any freely
│   ├── weather/  todo/  calorie/   # demo plugins
│   └── plugin-example/     # template for new plugins
├── routers/                # API endpoints
├── services/               # Core services (LLM, TTS, memory, mood, plugin loader, …)
│   └── tools/              # Unity-connected main features (costume, gacha, memory, …)
├── data/                   # Runtime data (gitignored; SQLite + plugin DBs)
└── tests/                  # Blackbox tests
```

## License

MIT

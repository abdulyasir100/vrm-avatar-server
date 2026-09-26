"""Multi-instance seams: path roots, plugin allowlist, TTS voice ref, prompt composition.

Run: python tests/test_instance_paths.py   (no server needed)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from services import llm, tts_service  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


_PATH_VARS = ["DATA_DIR", "AUDIO_DIR", "LOG_DIR", "PLUGIN_DATA_DIR", "RUNTIME_CONFIG_PATH",
              "USER_STATE_PATH", "BACKGROUND_DAILY_STATE_PATH", "COSTUMES_PATH",
              "MEMORY_DB_PATH", "STICKER_MAP_PATH", "PERSONALITY_PROMPT_PATH", "TOOL_DOCS_PATH"]


def fresh_config(tmp: Path, env_file_text: str = "", **env) -> dict:
    """Import config in a clean process: only ENV_FILE + the given vars, never the repo .env."""
    env_file = tmp / "instance.env"
    env_file.write_text(env_file_text, encoding="utf-8")
    base = {k: v for k, v in os.environ.items() if k not in _PATH_VARS}
    base.update(env, ENV_FILE=str(env_file))
    code = f"import json, config; print(json.dumps({{k: getattr(config, k) for k in {_PATH_VARS!r}}}))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=base,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)

    # --- defaults are the historic literals (a lone instance changes nothing) ---
    got = fresh_config(tmp)
    for key, want in {
        "DATA_DIR": "data", "AUDIO_DIR": "audio", "LOG_DIR": "logs",
        "PLUGIN_DATA_DIR": "data/plugins",
        "RUNTIME_CONFIG_PATH": "data/runtime_config.json",
        "USER_STATE_PATH": "data/user_state.json",
        "BACKGROUND_DAILY_STATE_PATH": "data/background_daily.json",
        "COSTUMES_PATH": "data/costumes.json",
        "MEMORY_DB_PATH": "data/memory.db",
        "STICKER_MAP_PATH": "data/stickers.json",
        "PERSONALITY_PROMPT_PATH": "prompts/personality.md",
        "TOOL_DOCS_PATH": "prompts/tools",
    }.items():
        check(f"default {key}", got[key], want)

    # --- one root moves everything under it; explicit vars still win ---
    got = fresh_config(tmp, DATA_DIR="/inst/nova/data", STICKER_MAP_PATH="/elsewhere/s.json")
    check("derived memory db", got["MEMORY_DB_PATH"], "/inst/nova/data/memory.db")
    check("derived plugin dir", got["PLUGIN_DATA_DIR"], "/inst/nova/data/plugins")
    check("derived user state", got["USER_STATE_PATH"], "/inst/nova/data/user_state.json")
    check("explicit override wins", got["STICKER_MAP_PATH"], "/elsewhere/s.json")

    # --- ENV_FILE is honoured ---
    got = fresh_config(tmp, env_file_text="DATA_DIR=/from/envfile\n")
    check("ENV_FILE loaded", got["MEMORY_DB_PATH"], "/from/envfile/memory.db")

    # --- plugin allowlist ---
    orig_plugins = config.PLUGINS_ENABLED
    for spec, name, want in [("", "todo", True), ("none", "todo", False), ("NONE", "todo", False),
                             ("todo, money", "money", True), ("todo,money", "clockin", False),
                             ("Todo", "todo", True)]:
        config.PLUGINS_ENABLED = spec
        check(f"plugin_allowed({spec!r}, {name})", config.plugin_allowed(name), want)
    config.PLUGINS_ENABLED = orig_plugins

    # --- TTS voice ref ---
    orig_ref = config.TTS_VOICE_REF
    config.TTS_VOICE_REF = ""
    check("payload without ref", tts_service._tts_payload("hi", "happy", "en"),
          {"text": "hi", "emotion": "HAPPY", "language": "en"})
    config.TTS_VOICE_REF = "ref-voice-ai/other.wav"
    check("payload with ref", tts_service._tts_payload("hi", "happy", "en").get("ref_audio"),
          "ref-voice-ai/other.wav")
    config.TTS_VOICE_REF = orig_ref

    # --- prompt composition: card + personality + rules, tokens filled ---
    card = tmp / "card.json"
    card.write_text(json.dumps({"name": "Test Character", "description": "A test."}), encoding="utf-8")
    personality = tmp / "personality.md"
    personality.write_text("You are {{CHARACTER_SHORT_NAME}}, close to {{OWNER_NAME}}.", encoding="utf-8")
    rules = tmp / "system.md"
    rules.write_text("Never break character. You are {{CHARACTER_NAME}}.", encoding="utf-8")

    saved = {k: getattr(config, k) for k in (
        "CHARACTER_CARD_PATH", "PERSONALITY_PROMPT_PATH", "SYSTEM_PROMPT_PATH",
        "CHARACTER_NAME", "CHARACTER_SHORT_NAME", "OWNER_NAME")}
    config.CHARACTER_CARD_PATH = str(card)
    config.PERSONALITY_PROMPT_PATH = str(personality)
    config.SYSTEM_PROMPT_PATH = str(rules)
    config.CHARACTER_NAME, config.CHARACTER_SHORT_NAME, config.OWNER_NAME = "Test Character", "Testy", "Owner"

    prompt = llm.load_system_prompt()
    check("card preamble first", prompt.startswith("Character: Test Character\nDescription: A test."), True)
    check("personality loaded", "You are Testy, close to Owner." in prompt, True)
    check("rules loaded + token", prompt.endswith("You are Test Character."), True)
    check("no raw tokens left", "{{" in prompt, False)

    config.PERSONALITY_PROMPT_PATH = str(tmp / "missing.md")
    check("missing personality is optional", "Testy" in llm.load_system_prompt(), False)

    card.write_text(json.dumps({"name": "Test Character", "system_prompt": "Card rules."}), encoding="utf-8")
    check("card system_prompt replaces rules file", llm.load_system_prompt().endswith("Card rules."), True)

    for k, v in saved.items():
        setattr(config, k, v)
    llm.load_system_prompt()

# --- the shipped prompt files carry no hardcoded identity ---
card_name = json.loads((ROOT / config.CHARACTER_CARD_PATH).read_text(encoding="utf-8")).get("short_name", "")
rules_text = (ROOT / "prompts" / "system.md").read_text(encoding="utf-8")
check("system.md is character-agnostic", bool(card_name) and card_name in rules_text, False)

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

"""Unit tests for services/settings_registry.py + runtime_settings persistence.

No server needed. Run: python tests/test_settings_registry.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import runtime_settings  # noqa: E402
from services import settings_registry as registry  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


def raises(name, fn):
    global failures
    try:
        fn()
    except ValueError as e:
        print(f"  [PASS] {name} ({e})")
        return
    failures += 1
    print(f"  [FAIL] {name}: no ValueError")


# --- registry integrity ---
for s in registry.SETTINGS:
    mod = registry._module(s)
    if not hasattr(mod, s.attr):
        failures += 1
        print(f"  [FAIL] {s.key}: {s.module}.{s.attr} does not exist")
check("keys unique", len(registry.REGISTRY), len(registry.SETTINGS))

# --- coerce ---
check("bool from 'on'", registry.coerce("tts_enabled", "on"), True)
check("bool from json false", registry.coerce("tts_enabled", False), False)
raises("bool rejects 'maybe'", lambda: registry.coerce("tts_enabled", "maybe"))
check("float from telegram string", registry.coerce("llm_temperature", "0.5"), 0.5)
raises("float above max", lambda: registry.coerce("llm_temperature", "9"))
check("int from '12'", registry.coerce("memory_context_hours", "12"), 12)
check("int from 12.0", registry.coerce("memory_context_hours", 12.0), 12)
raises("int rejects 1.5", lambda: registry.coerce("memory_context_hours", "1.5"))
raises("int below min", lambda: registry.coerce("sleep_hour", "-1"))
check("choice", registry.coerce("tts_language", "jp"), "jp")
raises("choice rejects fr", lambda: registry.coerce("tts_language", "fr"))
check("empty choice via 'none'", registry.coerce("sdk_effort", "none"), "")
check("empty choice via ''", registry.coerce("fallback_model", ""), "")
raises("no empty when not allowed", lambda: registry.coerce("cli_effort", "none"))
raises("unknown key", lambda: registry.coerce("nope", "1"))

# --- persistence (isolated temp file) ---
tmp = Path(tempfile.mkdtemp()) / "runtime_config.json"
runtime_settings._PATH = tmp
# The pre-registry file shape: every key snapshotted, incl. the old idle_tool_chance + junk.
tmp.write_text(json.dumps({
    "stt_enabled": False, "tts_language": "jp", "idle_tool_chance": 0.4,
    "sticker_chance": "bogus", "retired_key": 1,
}), encoding="utf-8")
orig = {k: registry.get(k) for k in ("stt_enabled", "tts_language", "sticker_chance", "idle_tool_chance")}
runtime_settings.apply()
check("apply bool", config.STT_ENABLED, False)
check("apply choice", config.TTS_LANGUAGE, "jp")
check("apply idle_tool_chance", registry.get("idle_tool_chance"), 0.4)
check("invalid saved value skipped", config.STICKER_CHANCE, orig["sticker_chance"])
check("defaults captured before apply", registry.default("tts_language"), orig["tts_language"])

runtime_settings.save({"llm_temperature": 0.5})
saved = json.loads(tmp.read_text(encoding="utf-8"))
check("merge-save keeps old keys", saved.get("tts_language"), "jp")
check("merge-save adds new key", saved.get("llm_temperature"), 0.5)
check("untouched key not frozen", "memory_soft_cap" in saved, False)
check("overridden_keys ignores unknown", "retired_key" in runtime_settings.overridden_keys(), False)

runtime_settings.reset("tts_language")
check("reset drops override", "tts_language" in json.loads(tmp.read_text(encoding="utf-8")), False)
check("no temp file left", os.path.exists(str(tmp) + ".tmp"), False)

sch = {row["key"]: row for row in registry.schema({"llm_temperature"})}
check("schema marks overridden", sch["llm_temperature"]["overridden"], True)
check("schema choices shape", sch["tts_language"]["choices"], ["en", "jp"])

# restore module state for anything run after this in-process
for k, v in orig.items():
    registry.assign(k, v)

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

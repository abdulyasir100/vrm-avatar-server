"""utils.time + locale/owner config from .env or the character card.

Run: python tests/test_local_time.py   (no server needed)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


_LOCALE_KEYS = ["TIMEZONE", "TZ_OFFSET_HOURS", "TZ_LABEL", "LANGUAGE", "CHARACTER_LANGUAGE", "LOCALE_COUNTRY",
                "OWNER_PRONOUN", "CHARACTER_SONGS", "CHARACTER_SONG_ALIASES",
                "BACKGROUND_POLL_SECONDS"]


def run_probe(tmp: Path, card: dict | None, probe_code: str, **env) -> dict:
    """Import config (+ utils.time) in a clean process: only the given env vars,
    never the repo .env, and an isolated character card."""
    card_path = tmp / "card.json"
    card_path.write_text(json.dumps(card or {}), encoding="utf-8")
    env_file = tmp / "instance.env"
    env_file.write_text("", encoding="utf-8")

    base = {k: v for k, v in os.environ.items() if k not in _LOCALE_KEYS
            and k not in ("TASK_REMINDER_POLL_SECONDS",)}
    base.update(env, ENV_FILE=str(env_file), CHARACTER_CARD_PATH=str(card_path),
                PYTHONIOENCODING="utf-8")
    code = (
        "import json, config\n"
        "config.load_character()\n"
        "from utils import time as ltime\n"
        f"{probe_code}\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=base,
                          capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"probe failed: {out.stderr}")
    return json.loads(out.stdout)


with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)

    # (a) no TZ config -> UTC offset 0, label ""
    got = run_probe(tmp, {}, "print(json.dumps({"
                    "'offset_seconds': ltime.now().utcoffset().total_seconds(),"
                    "'label': ltime.tz_label()}))")
    check("(a) no config -> offset 0", got["offset_seconds"], 0)
    check("(a) no config -> label ''", got["label"], "")

    # (b) card timezone + tz_label
    got = run_probe(tmp, {"timezone": "Asia/Jakarta", "tz_label": "WIB"},
                     "print(json.dumps({"
                     "'offset_seconds': ltime.now().utcoffset().total_seconds(),"
                     "'label': ltime.tz_label()}))")
    check("(b) card timezone -> +7h", got["offset_seconds"], 7 * 3600)
    check("(b) card tz_label -> WIB", got["label"], "WIB")

    # (c) env TIMEZONE overrides the card
    got = run_probe(tmp, {"timezone": "Asia/Jakarta", "tz_label": "WIB"},
                     "print(json.dumps({'tz': config.TIMEZONE}))",
                     TIMEZONE="Europe/Berlin")
    check("(c) env TIMEZONE overrides card", got["tz"], "Europe/Berlin")

    # (d) bogus TIMEZONE + TZ_OFFSET_HOURS fallback, no exception
    got = run_probe(tmp, {}, "print(json.dumps({"
                    "'offset_seconds': ltime.now().utcoffset().total_seconds()}))",
                    TIMEZONE="Not/AZone", TZ_OFFSET_HOURS="5.5")
    check("(d) bogus tz falls back to offset", got["offset_seconds"], 5.5 * 3600)

    # (e) fmt_time appends the label
    got = run_probe(tmp, {"tz_label": "WIB"}, "print(json.dumps({"
                    "'fmt': ltime.fmt_time(ltime.now().replace(hour=9, minute=5))}))")
    check("(e) fmt_time appends label", got["fmt"], "09:05 WIB")

    got = run_probe(tmp, {}, "print(json.dumps({"
                    "'fmt': ltime.fmt_time(ltime.now().replace(hour=9, minute=5))}))")
    check("(e) fmt_time no label", got["fmt"], "09:05")

    # (f) card owner.pronoun, language, country, songs land in config
    got = run_probe(tmp, {
        "owner": {"pronoun": "she"},
        "language": "id",
        "country": "Indonesia",
        "songs": ["Song A", "Song B"],
        "song_aliases": {"a": "Song A"},
    }, "print(json.dumps({"
       "'pronoun': config.OWNER_PRONOUN, 'language': config.LANGUAGE,"
       "'country': config.LOCALE_COUNTRY, 'songs': config.CHARACTER_SONGS,"
       "'aliases': config.CHARACTER_SONG_ALIASES}))")
    check("(f) owner.pronoun", got["pronoun"], "she")
    check("(f) language", got["language"], "id")
    check("(f) country", got["country"], "Indonesia")
    check("(f) songs", got["songs"], ["Song A", "Song B"])
    check("(f) song_aliases", got["aliases"], {"a": "Song A"})

    # (g) legacy TASK_REMINDER_POLL_SECONDS -> BACKGROUND_POLL_SECONDS
    got = run_probe(tmp, {}, "print(json.dumps({"
                    "'poll': config.BACKGROUND_POLL_SECONDS}))",
                    TASK_REMINDER_POLL_SECONDS="30")
    check("(g) legacy TASK_REMINDER_POLL_SECONDS", got["poll"], 30)

    # (g2) BACKGROUND_POLL_SECONDS itself still wins over the legacy var
    got = run_probe(tmp, {}, "print(json.dumps({"
                    "'poll': config.BACKGROUND_POLL_SECONDS}))",
                    TASK_REMINDER_POLL_SECONDS="30", BACKGROUND_POLL_SECONDS="45")
    check("(g2) BACKGROUND_POLL_SECONDS wins over legacy", got["poll"], 45)

    # (g3) default with neither set
    got = run_probe(tmp, {}, "print(json.dumps({"
                    "'poll': config.BACKGROUND_POLL_SECONDS}))")
    check("(g3) default BACKGROUND_POLL_SECONDS", got["poll"], 60)

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

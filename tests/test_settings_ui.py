"""Settings GUI: the page is served openly, carries no secret, and can render every registry setting.

Run: python tests/test_settings_ui.py   (no server needed)
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
from routers import admin, dashboard  # noqa: E402
from services import settings_registry as registry  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


import tempfile  # noqa: E402

from services import memory  # noqa: E402

_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
config.BRAIN_BACKEND, config.BRAIN_DB_PATH = "sqlite", str(Path(_tmp.name) / "brain.db")
memory.init(str(Path(_tmp.name) / "memory.db"))  # GET /admin/config reports memory counts

config.ADMIN_KEY = "test-admin-key-do-not-leak"
app = FastAPI()
app.include_router(dashboard.router)
app.include_router(admin.router)
client = TestClient(app)

page = client.get("/settings")
check("page is served without a key", (page.status_code, page.headers["content-type"].split(";")[0]), (200, "text/html"))
check("the key is never baked into the page", config.ADMIN_KEY in page.text, False)
check("the API behind it still needs the key", client.get("/admin/config").status_code in (401, 403), True)

schema = client.get("/admin/config", headers={"X-Admin-Key": config.ADMIN_KEY}).json()["schema"]
check("schema covers the whole registry", len(schema), len(registry.SETTINGS))
fields = {"key", "group", "type", "help", "min", "max", "choices", "value", "default", "overridden", "applies"}
check("every field the page reads is in the schema", [s["key"] for s in schema if not fields <= set(s)], [])

handled = set(re.findall(r'setting\.type === "(\w+)"', page.text))
free_text = {"str"}  # falls through to the text input
unrenderable = [s["key"] for s in schema if not s["choices"] and s["type"] not in handled | free_text]
check("every setting type has a control", unrenderable, [])
check("the page uses the three config verbs", [v in page.text for v in ('"GET", "/admin/config"', '"POST", "/admin/config"', '"DELETE", `/admin/config/')],
      [True, True, True])

# --- Plugins tab (step 1 of the settings GUI v2 spec) ---
check("plugins tab loads every plugin, not just loaded ones", '"/plugin/all"' in page.text, True)
check("plugins tab toggles", ["/enable`" in page.text, "/disable`" in page.text], [True, True])
check("plugins tab edits and resets settings",
      ['"PUT", `/plugin/' in page.text, '"DELETE", `/plugin/' in page.text], [True, True])
check("secret settings render as password inputs", "setting.secret" in page.text, True)
check("plugin settings reuse the shared control renderer", page.text.count("controlFor(") >= 3, True)

# --- provider-specific settings (Claude-only knobs hidden for other providers) ---
claude_only = {s.key for s in registry.SETTINGS if s.provider == "claude"}
check("the Claude-only knobs are tagged", claude_only >= {"sdk_effort", "cli_effort", "fallback_model", "warm_pool"}, True)
for provider, want in (("claude", True), ("openai", False)):
    config.LLM_PROVIDER = provider
    rows = client.get("/admin/config", headers={"X-Admin-Key": config.ADMIN_KEY}).json()["schema"]
    check(f"{provider}: Claude-only rows apply={want}", {r["applies"] for r in rows if r["key"] in claude_only}, {want})
    check(f"{provider}: untagged rows always apply", all(r["applies"] for r in rows if r["key"] not in claude_only), True)
    check(f"{provider}: schema still covers the whole registry", len(rows), len(registry.SETTINGS))
check("the page hides rows that don't apply", "setting.applies !== false" in page.text, True)

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

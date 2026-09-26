"""Plugin management endpoints: auth, toggles, settings, error codes, secrets masked.

Run: python tests/test_plugin_api.py   (no server needed)
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
from routers import plugin as plugin_router  # noqa: E402
from services import plugin_loader  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)
    plugin_loader._PLUGINS_DIR = tmp / "plugins"
    config.PLUGINS_ENABLED, config.DATA_DIR = "", str(tmp / "data")
    for name, enabled, handler in (("alpha", True, ""), ("beta", False, ""), ("broken", False, "raise RuntimeError('boom')\n")):
        d = plugin_loader._PLUGINS_DIR / name
        d.mkdir(parents=True)
        schema = {"count": {"type": "int", "default": 5, "label": "n"},
                  "token": {"type": "string", "secret": True, "description": "key"}}
        (d / "manifest.json").write_text(json.dumps({"name": name, "enabled": enabled, "settings_schema": schema}), encoding="utf-8")
        (d / "handler.py").write_text(handler, encoding="utf-8")
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()

    config.ADMIN_KEY = "k"
    app = FastAPI()
    app.include_router(plugin_router.router)
    client = TestClient(app, base_url="http://remote.example")  # not loopback -> key required
    H = {"X-Admin-Key": "k"}

    check("all needs the key", client.get("/plugin/all").status_code in (401, 403), True)
    names = [p["name"] for p in client.get("/plugin/all", headers=H).json()["plugins"]]
    check("all lists disabled plugins too", names, ["alpha", "beta", "broken"])
    check("list keeps meaning: loaded only", [p["name"] for p in client.get("/plugin/list", headers=H).json()["plugins"]], ["alpha"])

    r = client.post("/plugin/beta/enable", headers=H)
    check("enable beta", (r.status_code, r.json()["plugin"]["loaded"]), (200, True))
    check("enable response flags restart_required (chat tools need a restart)", r.json()["restart_required"], True)
    check("enable unknown -> 404", client.post("/plugin/ghost/enable", headers=H).status_code, 404)
    r = client.post("/plugin/broken/enable", headers=H)
    check("broken import -> 500 with reason", (r.status_code, "boom" in r.json()["detail"]), (500, True))
    config.PLUGINS_ENABLED = "alpha"
    check("locked -> 409", client.post("/plugin/beta/enable", headers=H).status_code, 409)
    config.PLUGINS_ENABLED = ""
    r = client.post("/plugin/beta/disable", headers=H)
    check("disable beta", (r.status_code, r.json()["plugin"]["loaded"]), (200, False))
    check("disable response flags restart_required too", r.json()["restart_required"], True)

    r = client.put("/plugin/alpha/settings", headers=H, json={"count": "8", "token": "sk-secret"})
    rows = {s["key"]: s for s in r.json()["settings"]}
    check("put settings", (r.status_code, rows["count"]["value"]), (200, 8))
    check("secret masked in response", "sk-secret" in r.text, False)
    check("secret masked in GET", "sk-secret" in client.get("/plugin/alpha/settings", headers=H).text, False)
    check("bad value -> 400", client.put("/plugin/alpha/settings", headers=H, json={"count": "x"}).status_code, 400)
    check("unknown key -> 400", client.put("/plugin/alpha/settings", headers=H, json={"nope": 1}).status_code, 400)
    r = client.put("/plugin/alpha/settings", headers=H, json={"count": 3, "nope": 1})
    check("a bad key in the batch writes nothing", (r.status_code, plugin_loader.get_setting("alpha", "count")), (400, 8))
    check("disabled plugin settings -> 409", client.put("/plugin/beta/settings", headers=H, json={"count": 1}).status_code, 409)
    check("unknown plugin settings -> 404", client.get("/plugin/ghost/settings", headers=H).status_code, 404)
    r = client.delete("/plugin/alpha/settings/count", headers=H)
    check("reset", {s["key"]: s for s in r.json()["settings"]}["count"]["value"], 5)

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)

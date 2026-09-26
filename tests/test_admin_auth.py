"""Unit tests for services/auth.py on the /admin router.

No server needed. Run: python tests/test_admin_auth.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
from routers import admin, memory_router, plugin  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


app = FastAPI()
app.include_router(admin.router)
app.include_router(memory_router.router)
app.include_router(plugin.router)
client = TestClient(app)  # its requests arrive from host "testclient" — a remote caller
local = TestClient(app, client=("127.0.0.1", 50000))

_orig = config.ADMIN_KEY


def status(method, path, headers=None):
    return client.request(method, path, headers=headers or {}, json={}).status_code


# --- /admin: every route sits behind the router-level dependency -----------------
config.ADMIN_KEY = ""
check("no key configured -> open", status("GET", "/admin/user-state"), 200)

config.ADMIN_KEY = "s3cret"
check("key set, no header -> 403", status("GET", "/admin/user-state"), 403)
check("key set, wrong header -> 403", status("GET", "/admin/user-state", {"X-Admin-Key": "nope"}), 403)
check("key set, non-ascii header -> 403", status("GET", "/admin/user-state", {"X-Admin-Key": "s3crét".encode("latin-1")}), 403)
check("key set, right header -> 200", status("GET", "/admin/user-state", {"X-Admin-Key": "s3cret"}), 200)
check("mutating route refused too", status("POST", "/admin/memory/clear"), 403)
check("delete route refused too", status("DELETE", "/admin/config/tts_enabled"), 403)

# --- /memory and /plugin share the admin secret; only /plugin trusts loopback ---------
import tempfile  # noqa: E402
from services import memory  # noqa: E402
memory.init(str(Path(tempfile.mkdtemp()) / "memory.db"))  # /memory/stats reads the DB
KEY = {"X-Admin-Key": "s3cret"}
check("memory, no header -> 403", status("GET", "/memory/stats"), 403)
check("memory, right header -> 200", status("GET", "/memory/stats", KEY), 200)
check("memory from loopback, no header -> 403", local.get("/memory/stats").status_code, 403)
check("admin from loopback, no header -> 403", local.get("/admin/user-state").status_code, 403)
check("plugin, remote, no header -> 403", status("GET", "/plugin/list"), 403)
check("plugin, remote, right header -> 200", status("GET", "/plugin/list", KEY), 200)
check("plugin from loopback, no header -> 200", local.get("/plugin/list").status_code, 200)
# Anything that came through a proxy/tunnel is not "local", whatever the peer address says.
for fwd in ({"X-Forwarded-For": "203.0.113.9"}, {"X-Forwarded-For": "127.0.0.1"}, {"CF-Connecting-IP": "203.0.113.9"},
            {"Forwarded": "for=127.0.0.1"}, {"X-Real-IP": "127.0.0.1"}, {"Via": "1.1 tunnel"}):
    check(f"plugin from loopback via proxy {list(fwd)[0]} -> 403", local.get("/plugin/list", headers=fwd).status_code, 403)
check("proxied loopback with the key -> 200",
      local.get("/plugin/list", headers={"X-Forwarded-For": "203.0.113.9", **KEY}).status_code, 200)

config.ADMIN_KEY = _orig

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

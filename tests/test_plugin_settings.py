"""Plugin settings: schema-typed, per instance, storage-backed when the plugin has a settings table.

Run: python tests/test_plugin_settings.py   (no server needed)
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import plugin_loader  # noqa: E402

failures = 0


def disable(name):
    """disable_plugin is async (it may await a plugin's shutdown())."""
    return asyncio.run(plugin_loader.disable_plugin(name))


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception as e:  # wrong exception type
        return f"{type(e).__name__}: {e}"
    return False


SCHEMA = {
    "count": {"type": "int", "default": 5, "label": "How many"},
    "loud": {"type": "bool", "default": True, "label": "Loud"},
    "mode": {"type": "string", "default": "medium", "label": "Mode", "options": ["low", "medium", "high"]},
    "token": {"type": "string", "description": "API token", "secret": True},
}
STORAGE = '''
_vals = {}
def init(path): pass
def get_setting(key): return _vals.get(key)
def set_setting(key, value): _vals[key] = value
'''


def make_plugin(root: Path, name: str, storage: bool) -> None:
    d = root / name
    d.mkdir(parents=True)
    manifest = {"name": name, "description": "t", "enabled": True, "settings_schema": SCHEMA}
    if storage:
        manifest["storage"] = {"type": "sqlite"}
        (d / "storage.py").write_text(STORAGE, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "handler.py").write_text("_storage = None\ndef set_storage(s):\n    global _storage\n    _storage = s\n",
                                  encoding="utf-8")


with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)
    plugin_loader._PLUGINS_DIR = tmp / "plugins"
    config.PLUGINS_ENABLED, config.DATA_DIR = "", str(tmp / "a")
    config.PLUGIN_DATA_DIR = str(tmp / "a" / "plugins")
    make_plugin(plugin_loader._PLUGINS_DIR, "jsonp", storage=False)
    make_plugin(plugin_loader._PLUGINS_DIR, "storep", storage=True)
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()

    for p in ("jsonp", "storep"):
        check(f"{p}: int default", plugin_loader.get_setting(p, "count"), 5)
        check(f"{p}: bool default", plugin_loader.get_setting(p, "loud"), True)
        check(f"{p}: set int from string", plugin_loader.set_setting(p, "count", "12"), 12)
        check(f"{p}: read back typed", plugin_loader.get_setting(p, "count"), 12)
        check(f"{p}: set bool", plugin_loader.set_setting(p, "loud", False), False)
        check(f"{p}: bool read back", plugin_loader.get_setting(p, "loud"), False)
        check(f"{p}: option enforced", raises(lambda: plugin_loader.set_setting(p, "mode", "extreme"), ValueError), True)
        check(f"{p}: bad int rejected", raises(lambda: plugin_loader.set_setting(p, "count", "abc"), ValueError), True)
        check(f"{p}: bool is not an int", raises(lambda: plugin_loader.set_setting(p, "count", True), ValueError), True)
        check(f"{p}: unknown key", raises(lambda: plugin_loader.get_setting(p, "nope"), KeyError), True)
        check(f"{p}: reset returns default", plugin_loader.reset_setting(p, "count"), 5)
        check(f"{p}: reset took effect", plugin_loader.get_setting(p, "count"), 5)

    storage = plugin_loader._loaded_plugins["storep"]["storage"]
    plugin_loader.set_setting("storep", "count", 7)
    check("storage-backed value lives in the plugin's own table", storage.get_setting("count"), "7")
    check("storage-backed value not in the JSON file",
          "storep" in plugin_loader._read_json(Path(config.DATA_DIR) / "plugin_settings.json"), False)
    storage.set_setting("loud", "0")  # what a chat command writes
    check("chat-command write is what the helper reads", plugin_loader.get_setting("storep", "loud"), False)

    # Review focus: junk left in storage by an old command -> default, no crash
    storage.set_setting("count", "abc")
    check("unparseable stored value falls back to default", plugin_loader.get_setting("storep", "count"), 5)

    # Review focus: secrets never leave in plaintext
    plugin_loader.set_setting("jsonp", "token", "sk-live-123")
    rows = {r["key"]: r for r in plugin_loader.get_settings("jsonp")}
    check("secret value masked", rows["token"]["value"], "set")
    check("secret never in any row", "sk-live-123" in json.dumps(plugin_loader.get_settings("jsonp")), False)
    check("helper still returns the real secret to code", plugin_loader.get_setting("jsonp", "token"), "sk-live-123")
    check("row shape matches /admin/config rows", sorted(rows["mode"]),
          ["choices", "default", "help", "key", "max", "min", "overridden", "secret", "type", "value"])
    check("string type normalised to str", rows["mode"]["type"], "str")
    check("options become choices", rows["mode"]["choices"], ["low", "medium", "high"])
    check("label becomes help", rows["count"]["help"], "How many")

    # Not loaded -> can't write
    disable("jsonp")
    check("disabled plugin can still be read", plugin_loader.get_setting("jsonp", "count"), 5)
    check("disabled plugin can't be written",
          raises(lambda: plugin_loader.set_setting("jsonp", "count", 3), PermissionError), True)

    # Review focus: corrupt settings file == empty
    (Path(config.DATA_DIR) / "plugin_settings.json").write_text("][", encoding="utf-8")
    check("corrupt settings file falls back to default", plugin_loader.get_setting("jsonp", "count"), 5)

    # Review focus: second instance doesn't see instance A's JSON settings
    plugin_loader.enable_plugin("jsonp")
    plugin_loader.set_setting("jsonp", "count", 9)
    config.DATA_DIR = str(tmp / "b")
    check("instance B sees the default", plugin_loader.get_setting("jsonp", "count"), 5)

    check("list_all carries settings rows", len(next(p for p in plugin_loader.list_all() if p["name"] == "jsonp")["settings"]), 4)

    # F5: a manifest default that fails _coerce must not blank the whole /plugin/all list —
    # only that plugin's row loses its settings (with an error), everyone else is fine.
    config.DATA_DIR = str(tmp / "a")  # back to instance A, where jsonp/storep already work
    bad_dir = plugin_loader._PLUGINS_DIR / "badschema"
    bad_dir.mkdir(parents=True)
    (bad_dir / "manifest.json").write_text(json.dumps({
        "name": "badschema", "description": "t", "enabled": True,
        "settings_schema": {"count": {"type": "int", "default": "abc"}},
    }), encoding="utf-8")
    (bad_dir / "handler.py").write_text("", encoding="utf-8")
    plugin_loader.enable_plugin("badschema")
    rows = {p["name"]: p for p in plugin_loader.list_all()}
    check("badschema still appears in list_all", "badschema" in rows, True)
    check("badschema row has no settings and carries an error", (rows["badschema"]["settings"], "error" in rows["badschema"]), ([], True))
    check("other plugins unaffected by badschema's bad default", len(rows["jsonp"]["settings"]) > 0, True)

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)

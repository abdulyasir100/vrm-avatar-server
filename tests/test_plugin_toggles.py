"""Plugin on/off is per instance (DATA_DIR/plugin_state.json) and never touches manifest.json.

Run: python tests/test_plugin_toggles.py   (no server needed)
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import plugin_loader, tool_registry  # noqa: E402

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


def make_plugin(root: Path, name: str, enabled: bool = True, handler: str | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    manifest = {"name": name, "description": f"{name} test", "enabled": enabled,
                "tools": [{"name": f"{name}_tool", "description": "t", "parameters": {}}]}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (d / "handler.py").write_text(handler if handler is not None else
                                  f"async def handle_{name}_tool(arg, context):\n    return 'ok'\n", encoding="utf-8")
    return d


with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)
    plugins_dir, data_a, data_b = tmp / "plugins", tmp / "a", tmp / "b"
    plugin_loader._PLUGINS_DIR = plugins_dir
    config.PLUGINS_ENABLED = ""
    alpha = make_plugin(plugins_dir, "alpha")
    make_plugin(plugins_dir, "beta", enabled=False)
    make_plugin(plugins_dir, "broken", enabled=False, handler="raise RuntimeError('boom')\n")
    manifest_bytes = (alpha / "manifest.json").read_bytes()

    config.DATA_DIR = str(data_a)
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("manifest default: alpha loads, beta doesn't", sorted(plugin_loader._loaded_plugins), ["alpha"])

    check("disable alpha", disable("alpha"), True)
    check("alpha unloaded", "alpha" in plugin_loader._loaded_plugins, False)
    check("alpha tool unregistered", tool_registry.get("alpha_tool") is None, True)
    check("manifest.json untouched", (alpha / "manifest.json").read_bytes() == manifest_bytes, True)
    state = json.loads((data_a / "plugin_state.json").read_text(encoding="utf-8"))
    check("state file records it", state, {"enabled": [], "disabled": ["alpha"]})

    check("enable beta (manifest says off)", plugin_loader.enable_plugin("beta"), True)
    check("beta loaded live", "beta" in plugin_loader._loaded_plugins, True)

    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("restart honours state over manifest", sorted(plugin_loader._loaded_plugins), ["beta"])

    # Review focus: a failing import leaves the server up and the state off
    try:
        plugin_loader.enable_plugin("broken")
        check("broken plugin raises", "no error", "error")
    except RuntimeError:
        check("broken plugin raises", "error", "error")
    check("broken stays off in state", "broken" in json.loads((data_a / "plugin_state.json").read_text())["enabled"], False)

    # Review focus: second instance sharing the checkout is unaffected
    config.DATA_DIR = str(data_b)
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("instance B still on manifest defaults", sorted(plugin_loader._loaded_plugins), ["alpha"])

    # Allowlist is the ceiling
    config.PLUGINS_ENABLED = "beta"
    check("locked_reason for blocked plugin", plugin_loader.locked_reason("alpha"), "not in PLUGINS_ENABLED")
    check("no lock for allowed plugin", plugin_loader.locked_reason("beta"), "")
    config.PLUGINS_ENABLED = ""

    # Review focus: corrupt state file == empty
    data_b.mkdir(exist_ok=True)  # never written yet in this instance; write needs the dir first
    (data_b / "plugin_state.json").write_text("{not json", encoding="utf-8")
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("corrupt state file falls back to manifest", sorted(plugin_loader._loaded_plugins), ["alpha"])

    listed = {p["name"]: p for p in plugin_loader.list_all()}
    check("list_all covers disabled plugins", sorted(listed), ["alpha", "beta", "broken"])
    check("list_all row shape", sorted(listed["beta"]),
          ["commands", "description", "enabled", "loaded", "locked_reason", "name", "settings"])
    check("list_all enabled/loaded", (listed["alpha"]["enabled"], listed["beta"]["loaded"]), (True, False))

    # F6: state-file "enabled" can never override the PLUGINS_ENABLED ceiling at load time.
    config.PLUGINS_ENABLED = ""
    make_plugin(plugins_dir, "gamma", enabled=False)
    config.DATA_DIR = str(tmp / "f6")
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("gamma off by manifest default", "gamma" in plugin_loader._loaded_plugins, False)
    plugin_loader._set_state("gamma", True)  # this instance's state says "on"
    config.PLUGINS_ENABLED = "alpha,beta"    # ceiling excludes gamma
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("F6: state-enabled but not in PLUGINS_ENABLED still doesn't load",
          "gamma" in plugin_loader._loaded_plugins, False)
    config.PLUGINS_ENABLED = ""

    # F1: disable_plugin calls the handler's shutdown() (sync or async), and an exception
    # in shutdown is swallowed — the toggle still succeeds.
    make_plugin(plugins_dir, "delta", enabled=True, handler=(
        "async def handle_delta_tool(arg, context):\n    return 'ok'\n\n"
        "shutdown_called = []\n\n\ndef shutdown():\n    shutdown_called.append(True)\n"
    ))
    make_plugin(plugins_dir, "epsilon", enabled=True, handler=(
        "async def handle_epsilon_tool(arg, context):\n    return 'ok'\n\n"
        "shutdown_called = []\n\n\nasync def shutdown():\n    shutdown_called.append(True)\n"
    ))
    make_plugin(plugins_dir, "zeta", enabled=True, handler=(
        "async def handle_zeta_tool(arg, context):\n    return 'ok'\n\n"
        "def shutdown():\n    raise RuntimeError('shutdown boom')\n"
    ))
    config.DATA_DIR = str(tmp / "f1")
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("delta/epsilon/zeta all loaded",
          sorted(n for n in ("delta", "epsilon", "zeta") if n in plugin_loader._loaded_plugins),
          ["delta", "epsilon", "zeta"])

    delta_handler = plugin_loader._loaded_plugins["delta"]["handler"]
    epsilon_handler = plugin_loader._loaded_plugins["epsilon"]["handler"]

    check("disable delta (sync shutdown) succeeds", disable("delta"), True)
    check("sync shutdown ran", delta_handler.shutdown_called, [True])
    check("delta unloaded", "delta" in plugin_loader._loaded_plugins, False)

    check("disable epsilon (async shutdown) succeeds", disable("epsilon"), True)
    check("async shutdown ran", epsilon_handler.shutdown_called, [True])

    check("disable zeta succeeds despite shutdown raising", disable("zeta"), True)
    check("zeta unloaded despite shutdown raising", "zeta" in plugin_loader._loaded_plugins, False)

    # F3: a plugin whose folder name differs from its manifest "name" is fully manageable
    # by its manifest name — enable/disable/list/get_setting all resolve through it.
    theta_dir = tmp / "plugins" / "theta-folder"
    theta_dir.mkdir(parents=True)
    (theta_dir / "manifest.json").write_text(json.dumps({
        "name": "theta", "description": "folder != manifest name", "enabled": False,
        "tools": [{"name": "theta_tool", "description": "t", "parameters": {}}],
        "settings_schema": {"count": {"type": "int", "default": 3, "label": "n"}},
    }), encoding="utf-8")
    (theta_dir / "handler.py").write_text(
        "async def handle_theta_tool(arg, context):\n    return 'ok'\n", encoding="utf-8")
    config.DATA_DIR = str(tmp / "f3")
    plugin_loader._loaded_plugins.clear()
    plugin_loader.load_all_plugins()
    check("theta off by default (folder name != manifest name)", "theta" in plugin_loader._loaded_plugins, False)
    check("F3: enable by manifest name", plugin_loader.enable_plugin("theta"), True)
    check("F3: theta loaded live", "theta" in plugin_loader._loaded_plugins, True)
    check("F3: get_setting resolves by manifest name", plugin_loader.get_setting("theta", "count"), 3)
    theta_row = next(p for p in plugin_loader.list_all() if p["name"] == "theta")
    check("F3: list_all lists it under the manifest name with its settings",
          (theta_row["loaded"], len(theta_row["settings"])), (True, 1))
    check("F3: disable by manifest name", disable("theta"), True)
    check("F3: theta unloaded", "theta" in plugin_loader._loaded_plugins, False)

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)

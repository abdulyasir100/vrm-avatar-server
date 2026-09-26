"""Plugin background-check nags: one generic path, notify honoured, no doubling.

Run: python tests/test_background_nags.py   (no server needed)

A prompt-based background_check item may carry `notify` (a Telegram push
payload, e.g. the money plugin's spending nag). After one loop tick the prompt
is enqueued once and ntfy.notify is called once with that payload; a second
tick the same day does neither; a different `type` fires again.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="bgnags-")
_env_file = Path(_tmp) / "instance.env"
_env_file.write_text("", encoding="utf-8")
os.environ["ENV_FILE"] = str(_env_file)
os.environ["DATA_DIR"] = _tmp
os.environ.pop("BACKGROUND_DAILY_STATE_PATH", None)

from services import background as bg  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {'' if cond else detail}")
    if not cond:
        failures += 1


NOTIFY = {"title": "t", "message": "m"}
_items = {"type": "spend_80"}


def fake_check():
    return [{"type": _items["type"], "prompt": "p", "context": "spending_nag", "notify": dict(NOTIFY)}]


class _FakeHandler:
    check_spending = staticmethod(fake_check)


async def _fake_checks():
    return [("money", fake_check)]


enqueued: list = []
notified: list = []


async def _fake_enqueue(item):
    enqueued.append(item)


async def _fake_notify(**kw):
    notified.append(kw)


bg.plugin_loader.get_plugin_background_checks = _fake_checks
bg.plugin_loader.get_plugin = lambda name: {"handler": _FakeHandler()} if name == "money" else None
bg.enqueue = _fake_enqueue
bg.ntfy.notify = _fake_notify
bg._check_fired_today.clear()


async def tick():
    """The plugin-check steps of one _main_loop pass."""
    await bg._check_reminders()
    if hasattr(bg, "_check_spending"):  # removed: folded into the generic path
        await bg._check_spending()


def main():
    print("tick 1: fires once, with notify")
    asyncio.run(tick())
    check("prompt enqueued once", len(enqueued) == 1, f"got {len(enqueued)}")
    check("notify called once", len(notified) == 1, f"got {notified}")
    check("notify payload passed through", notified[:1] == [NOTIFY], f"got {notified}")

    print("tick 2 same day: silent")
    enqueued.clear(); notified.clear()
    asyncio.run(tick())
    check("no second enqueue", len(enqueued) == 0, f"got {len(enqueued)}")
    check("no second notify", len(notified) == 0, f"got {notified}")

    print("different type: fires again")
    _items["type"] = "spend_100"
    enqueued.clear(); notified.clear()
    asyncio.run(tick())
    check("new type enqueued once", len(enqueued) == 1, f"got {len(enqueued)}")
    check("new type notify once", len(notified) == 1, f"got {notified}")

    print("legacy spending loop removed")
    check("_check_spending gone", not hasattr(bg, "_check_spending"))

    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

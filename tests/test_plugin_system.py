"""Comprehensive blackbox test for avatar-server.

Usage:
    python tests/test_plugin_system.py [--base http://100.102.146.85:8800]

Tests 16 sections covering plugins, main features, intent gating, idle talk,
thinking leaks, inline keyboards, /p.* commands, and character-agnostic config.
"""

import asyncio
import sys
import time
import os
import httpx

# Fix Windows console encoding for emoji
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--base" else "http://100.102.146.85:8800"
DELAY = 10  # seconds between LLM calls
RESULTS = []


def record(name, passed, details=""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append({"name": name, "passed": passed, "details": details})
    print(f"  [{status}] {name}" + (f" -- {details}" if details else ""))


async def chat(client, message, user_name="Venomaru"):
    resp = await client.post(f"{BASE}/chat", json={
        "message": message, "context": "test", "user_name": user_name,
    }, timeout=90)
    resp.raise_for_status()
    return resp.json()


async def get(client, path):
    resp = await client.get(f"{BASE}{path}", timeout=15)
    resp.raise_for_status()
    return resp.json()


async def post(client, path, data):
    resp = await client.post(f"{BASE}{path}", json=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


async def run_tests():
    async with httpx.AsyncClient() as c:
        print(f"\n{'='*60}")
        print(f"Avatar Server Blackbox Test")
        print(f"Server: {BASE}")
        print(f"{'='*60}\n")

        # ========== 1. Server Status ==========
        print("--- 1. Server Status ---")
        try:
            status = await get(c, "/status")
            record("Server online", status.get("server") == "ok")
        except Exception as e:
            record("Server online", False, str(e))
            print("\nServer unreachable. Aborting.")
            return

        # ========== 2. Plugin List ==========
        print("\n--- 2. Plugin List ---")
        try:
            pl = await get(c, "/plugin/list")
            names = [p["name"] for p in pl.get("plugins", [])]
            expected = ["anime", "calendar", "calorie", "meme", "money", "prayer", "rss", "todo", "weather"]
            record("Plugin list endpoint", len(names) > 0, f"loaded={names}")
            for exp in expected:
                record(f"Plugin '{exp}' loaded", exp in names)
        except Exception as e:
            record("Plugin list endpoint", False, str(e))

        # ========== 3. Character Config ==========
        print("\n--- 3. Character Config ---")
        try:
            status = await get(c, "/status")
            # Character name should appear in a notification test
            r = await chat(c, "hi")
            record("Chat responds", bool(r.get("reply")), f"reply={r.get('reply', '')[:60]}")
            await asyncio.sleep(DELAY)
        except Exception as e:
            record("Character config", False, str(e))

        # ========== 4. Intent Gating ==========
        print("\n--- 4. Intent Gating ---")
        intent_tests = [
            ("sui-chan add task: blackbox alpha", True, "Nickname + task"),
            ("add task: blackbox beta", True, "Action word + task"),
            ("I need to do my tasks today", False, "Casual mention"),
            ("spent 20k on food", True, "Action word + money"),
            ("I wish I had more money lol", False, "Casual money"),
            ("ate nasi goreng", True, "Action word + food"),
            ("I love food so much", False, "Casual food"),
        ]
        for msg, should_trigger, label in intent_tests:
            r = await chat(c, msg)
            triggered = r.get("tool_executed") is not None
            record(f"Intent: {label}", triggered == should_trigger,
                   f"tool={r.get('tool_executed')}")
            await asyncio.sleep(DELAY)

        # ========== 5. Todo CRUD ==========
        print("\n--- 5. Todo CRUD ---")
        r = await chat(c, "add task: blackbox test task")
        record("Add task", r.get("tool_executed") == "add_task", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        r = await chat(c, "show my tasks")
        record("List tasks", r.get("tool_executed") == "list_tasks", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        r = await chat(c, "done with blackbox test task")
        record("Complete task", r.get("tool_executed") == "complete_task", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        # ========== 6. Money CRUD ==========
        print("\n--- 6. Money CRUD ---")
        r = await chat(c, "spent 5000 on blackbox snack")
        record("Add expense", r.get("tool_executed") == "add_expense", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        r = await chat(c, "check my balance")
        record("Check balance", r.get("tool_executed") == "check_balance", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        # ========== 7. Calorie ==========
        print("\n--- 7. Calorie ---")
        r = await chat(c, "ate some blackbox pizza for lunch")
        record("Add meal", r.get("tool_executed") == "add_meal", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        r = await chat(c, "check my calories")
        record("Check calories", r.get("tool_executed") == "check_calories", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        # ========== 8. Weather ==========
        print("\n--- 8. Weather ---")
        r = await chat(c, "how's the weather?")
        record("Check weather", r.get("tool_executed") == "check_weather", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        # ========== 9. Main Features ==========
        print("\n--- 9. Main Features ---")
        r = await chat(c, "change to maid outfit")
        record("Costume change", r.get("tool_executed") == "change_costume", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        r = await chat(c, "remember that this is a blackbox test")
        record("Save memory", r.get("tool_executed") == "save_memory", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        # ========== 10. Sensor ==========
        print("\n--- 10. Sensor ---")
        r = await chat(c, "how's my screen time?")
        record("Check screen time", r.get("tool_executed") == "check_screen_time", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        r = await chat(c, "how many steps today?")
        record("Check steps", r.get("tool_executed") == "check_steps", f"tool={r.get('tool_executed')}")
        await asyncio.sleep(DELAY)

        # ========== 11. Thinking Leak ==========
        print("\n--- 11. Thinking Leak ---")
        leak_messages = [
            "I'm feeling really tired today",
            "do you love me?",
            "what should I eat for dinner?",
            "I'm so stressed about work",
            "tell me something nice",
        ]
        leak_patterns = [
            "As Suisei", "As Sui-chan", "I should", "I need to", "The user",
            "Keep it", "Stay in character", "One sentence", "In character",
            "<thinking>", "</thinking>", "I have access", "The system",
        ]
        for msg in leak_messages:
            r = await chat(c, msg)
            reply = r.get("reply", "")
            leaked = any(p.lower() in reply.lower() for p in leak_patterns)
            record(f"No leak: '{msg[:30]}'", not leaked,
                   f"leaked pattern found" if leaked else f"clean: {reply[:50]}")
            await asyncio.sleep(DELAY)

        # ========== 12. Idle Talk ==========
        print("\n--- 12. Idle Talk ---")
        try:
            await post(c, "/admin/config", {"idle_talk_hours": 0.001667})
            record("Set idle to 6s", True)
        except Exception as e:
            record("Set idle to 6s", False, str(e))

        print("    Waiting 90s for idle talk...")
        await asyncio.sleep(90)

        try:
            s = await get(c, "/status")
            idle_since = s.get("idle_seconds_since_interaction", 999)
            record("Idle talk fired", idle_since < 60, f"idle_since={idle_since}s")
        except Exception as e:
            record("Idle talk fired", False, str(e))

        try:
            await post(c, "/admin/config", {"idle_talk_hours": 0.5})
            record("Restored idle to 30min", True)
        except Exception as e:
            record("Restored idle to 30min", False, str(e))

        # ========== 13. /p.* Commands ==========
        print("\n--- 13. Plugin Commands ---")
        cmd_tests = [
            ("todo", "tasks", "tasks"),
            ("money", "balance", "balance"),
            ("calorie", "calories", "calories"),
            ("weather", "weather", "weather"),
            ("prayer", "prayer", "prayer"),
        ]
        for plugin, cmd, label in cmd_tests:
            try:
                r = await post(c, "/plugin/command", {"plugin": plugin, "command": cmd, "args": ""})
                record(f"/p.{label} command", r.get("ok", False), f"text={r.get('text', '')[:50]}")
            except Exception as e:
                record(f"/p.{label} command", False, str(e))

        # ========== 14. Inline Callbacks ==========
        print("\n--- 14. Inline Callbacks ---")
        # Add a task first via command, then try callback
        try:
            await post(c, "/plugin/command", {"plugin": "todo", "command": "tasks.add", "args": "callback test"})
            # Get task list to find the ID
            r = await post(c, "/plugin/command", {"plugin": "todo", "command": "tasks", "args": ""})
            # Try callback (even if we don't have exact ID, test the endpoint)
            cb = await post(c, "/plugin/callback", {"plugin": "todo", "action": "done", "item_id": "fake"})
            record("Callback endpoint works", True, f"ok={cb.get('ok')}")
        except Exception as e:
            record("Callback endpoint works", False, str(e))

        # ========== 15. /plugin/guide ==========
        print("\n--- 15. Plugin Guide ---")
        try:
            guide = await get(c, "/plugin/guide")
            sections = guide.get("sections", [])
            record("Guide returns content", len(sections) > 0, f"sections={len(sections)}")
            has_todo = any(s["name"] == "todo" for s in sections)
            record("Guide includes todo", has_todo)
        except Exception as e:
            record("Guide endpoint", False, str(e))

        # ========== 16. Cleanup ==========
        print("\n--- 16. Cleanup ---")
        try:
            # Clean test messages from memory
            await post(c, "/admin/memory/search-delete", {"query": "blackbox"})
            await post(c, "/admin/memory/search-delete", {"query": "callback test"})
            record("Memory cleanup", True)
        except Exception:
            # Fallback: direct SQL cleanup
            try:
                r = await chat(c, "forget about the blackbox test")
                record("Memory cleanup (via chat)", True)
            except Exception:
                record("Memory cleanup", False, "manual cleanup needed")

        # ========== Summary ==========
        print(f"\n{'='*60}")
        passed = sum(1 for r in RESULTS if r["passed"])
        failed = sum(1 for r in RESULTS if not r["passed"])
        total = len(RESULTS)
        print(f"Results: {passed}/{total} passed, {failed} failed")

        if failed:
            print(f"\nFailed tests:")
            for r in RESULTS:
                if not r["passed"]:
                    print(f"  FAIL: {r['name']} -- {r['details']}")

        print(f"{'='*60}\n")
        return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)

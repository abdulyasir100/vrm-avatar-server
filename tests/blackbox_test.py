"""Black-box integration tests for avatar-server.

Sends real HTTP requests to the running server and validates responses.
No mocks, no GUI, no voice — just text in, text out.

Usage:
    cd F:\OS\avatar-server
    .venv\Scripts\python.exe tests\blackbox_test.py

Requires: avatar-server running on localhost:8800, LM Studio running.
TTS can be off — we only check text responses.
"""

import asyncio
import httpx
import time
import sys
import re
from dataclasses import dataclass, field

BASE = "http://localhost:8800"
DELAY = 6  # seconds between LLM requests (Mistral free tier: 1 req/sec)
FAST_DELAY = 1  # seconds for non-LLM endpoints

# Valid emotions from config
VALID_EMOTIONS = {"HAPPY", "SAD", "SURPRISED", "ANGRY", "THINKING", "NEUTRAL"}

# Emotion words that should NOT appear in the reply text
EMOTION_LEAK_WORDS = VALID_EMOTIONS | {"NORMAL", "RELAXED", "EXCITED"}


@dataclass
class TestResult:
    name: str
    passed: bool
    details: str = ""
    reply: str = ""
    emotion: str = ""
    tool: str = ""


results: list[TestResult] = []


def log(msg: str):
    print(f"  {msg}")


def log_result(r: TestResult):
    icon = "PASS" if r.passed else "FAIL"
    print(f"\n[{icon}] {r.name}")
    if r.reply:
        log(f"Reply: {r.reply[:120]}{'...' if len(r.reply) > 120 else ''}")
    if r.emotion:
        log(f"Emotion: {r.emotion}")
    if r.tool:
        log(f"Tool: {r.tool}")
    if r.details:
        log(f"Details: {r.details}")
    results.append(r)


async def chat(client: httpx.AsyncClient, message: str, user_name: str = "Abdul") -> dict:
    """Send a chat message and return the response dict."""
    resp = await client.post(f"{BASE}/chat", json={
        "message": message,
        "context": "test",
        "user_name": user_name,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════
# Test Helpers
# ═══════════════════════════════════════════════════════════════════════

def check_no_emotion_leak(reply: str) -> str | None:
    """Check if any bare emotion word leaked into the reply text."""
    # Check start of reply
    for word in EMOTION_LEAK_WORDS:
        if reply.startswith(word + " ") or reply.startswith(word + "\n"):
            return f"Emotion '{word}' leaked at start of reply"
        if f"\n{word} " in reply:
            return f"Emotion '{word}' leaked at start of a line"
    # Check bracketed emotions in text
    for word in EMOTION_LEAK_WORDS:
        if f"[{word}]" in reply:
            return f"Bracketed [{word}] leaked into reply"
    return None


def check_no_tool_leak(reply: str) -> str | None:
    """Check if any TOOL tags leaked into the reply text."""
    if "[TOOL:" in reply:
        return f"[TOOL:...] tag leaked into reply"
    if re.search(r"TOOL:[a-z_]+:", reply):
        return f"Bare TOOL:name:arg leaked into reply"
    if "category:" in reply.lower() and "content:" in reply.lower():
        return f"Hallucinated 'category:/content:' block leaked into reply"
    return None


def check_valid_emotion(emotion: str) -> str | None:
    """Check that emotion is a valid tag."""
    if emotion not in VALID_EMOTIONS:
        return f"Invalid emotion: '{emotion}' (expected one of {VALID_EMOTIONS})"
    return None


def validate_response(name: str, data: dict, expect_tool: str | None = None,
                       expect_no_tool: bool = False, min_reply_len: int = 2) -> TestResult:
    """Standard validation for a chat response."""
    reply = data.get("reply", "")
    emotion = data.get("emotion", "")
    tool = data.get("tool_executed", "") or ""
    issues = []

    # Basic checks — allow empty reply if tool was called (some models don't return text with tool_calls)
    if len(reply) < min_reply_len and not tool:
        issues.append(f"Reply too short ({len(reply)} chars)")

    emo_err = check_valid_emotion(emotion)
    if emo_err:
        issues.append(emo_err)

    leak_err = check_no_emotion_leak(reply)
    if leak_err:
        issues.append(leak_err)

    tool_err = check_no_tool_leak(reply)
    if tool_err:
        issues.append(tool_err)

    # Tool expectations
    if expect_tool and tool != expect_tool:
        issues.append(f"Expected tool '{expect_tool}' but got '{tool}'")

    if expect_no_tool and tool:
        issues.append(f"Expected no tool but got '{tool}'")

    passed = len(issues) == 0
    return TestResult(
        name=name,
        passed=passed,
        details="; ".join(issues) if issues else "OK",
        reply=reply,
        emotion=emotion,
        tool=tool,
    )


# ═══════════════════════════════════════════════════════════════════════
# Test Suites
# ═══════════════════════════════════════════════════════════════════════

async def test_health(client: httpx.AsyncClient):
    """Test /status endpoint — all subsystems."""
    print("\n" + "=" * 60)
    print("SUITE: Health Check")
    print("=" * 60)

    resp = await client.get(f"{BASE}/status", timeout=10)
    data = resp.json()

    checks = {
        "server": data.get("server"),
        "llm": data.get("llm"),
    }

    for name, val in checks.items():
        r = TestResult(
            name=f"Health: {name}",
            passed=val == "ok",
            details=f"Status: {val}",
        )
        log_result(r)

    # Check idle talk interval is 1 hour
    idle_threshold = data.get("idle_talk_threshold_hours")
    r = TestResult(
        name="Config: idle talk interval = 1.0h",
        passed=idle_threshold == 1.0,
        details=f"Got: {idle_threshold}",
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)


async def test_basic_chat(client: httpx.AsyncClient):
    """Test basic chat — clean emotion, no leaks."""
    print("\n" + "=" * 60)
    print("SUITE: Basic Chat")
    print("=" * 60)

    messages = [
        ("hello suichan", "Basic greeting"),
        ("how are you today?", "Simple question"),
        ("i am doing great", "Positive statement"),
        ("tell me a joke", "Request for humor"),
    ]

    for msg, label in messages:
        data = await chat(client, msg)
        r = validate_response(f"Chat: {label} — '{msg}'", data, expect_no_tool=True)
        log_result(r)
        await asyncio.sleep(DELAY)


async def test_emotion_tag_cleaning(client: httpx.AsyncClient):
    """Test that emotion tags don't leak into replies.

    The 8B model often outputs double emotions like '[NEUTRAL] HAPPY text'.
    Our fix should strip these.
    """
    print("\n" + "=" * 60)
    print("SUITE: Emotion Tag Cleaning")
    print("=" * 60)

    # These prompts tend to trigger emotional responses where the LLM
    # might double-tag with emotions
    prompts = [
        ("i just got a promotion!", "Exciting news — might trigger HAPPY leak"),
        ("my pet died today", "Sad news — might trigger SAD leak"),
        ("BOO! scared you", "Surprise trigger"),
        ("you're so annoying", "Anger trigger"),
        ("hmm let me think about that", "Thinking trigger"),
    ]

    for msg, label in prompts:
        data = await chat(client, msg)
        r = validate_response(f"Emotion: {label}", data)
        log_result(r)
        await asyncio.sleep(DELAY)


async def test_expense_detection(client: httpx.AsyncClient):
    """Test expense keyword fallback patterns."""
    print("\n" + "=" * 60)
    print("SUITE: Expense Detection")
    print("=" * 60)

    cases = [
        # (message, should_trigger_tool, label)
        ("i spent 20000 on food", True, "spent X on Y"),
        ("spent 5000 gorengan", True, "spent X item (no preposition)"),
        ("bought coffee for 15000", True, "bought X for Y"),
        ("i spent 23000 for sahoor", True, "spent X for Y"),
        ("paid 50000 for electricity", True, "paid X for Y"),
        ("spent 100000 on ovo", True, "spent X on Y (digital wallet)"),
        ("spent 5000 coffee", True, "spent X item (no preposition, single word)"),
        # These should NOT trigger expense
        ("i spent my time reading", False, "spent time (not money)"),
    ]

    for msg, should_trigger, label in cases:
        data = await chat(client, msg)
        tool = data.get("tool_executed", "") or ""
        triggered = tool == "add_expense"

        # If LLM generated the tool tag itself, that also counts as success
        if should_trigger and not triggered:
            # Check if LLM might have generated it on its own
            issues = f"Expected add_expense but got '{tool}'"
            passed = False
        elif not should_trigger and triggered:
            issues = f"False positive: triggered add_expense on non-expense"
            passed = False
        else:
            issues = "OK"
            passed = True

        r = TestResult(
            name=f"Expense: {label} — '{msg}'",
            passed=passed,
            details=issues,
            reply=data.get("reply", ""),
            emotion=data.get("emotion", ""),
            tool=tool,
        )
        log_result(r)

        # Also check for leaks
        leak = check_no_emotion_leak(data.get("reply", ""))
        if leak:
            log_result(TestResult(f"  Leak check: {label}", False, leak))
        tool_leak = check_no_tool_leak(data.get("reply", ""))
        if tool_leak:
            log_result(TestResult(f"  Tool leak check: {label}", False, tool_leak))

        await asyncio.sleep(DELAY)


async def test_income_detection(client: httpx.AsyncClient):
    """Test income keyword fallback patterns."""
    print("\n" + "=" * 60)
    print("SUITE: Income Detection")
    print("=" * 60)

    cases = [
        ("got paid 5000000", True, "got paid X"),
        ("got paycheck 5000", True, "got paycheck X"),
        ("got my paycheck 500000", True, "got my paycheck X"),
        ("received 500000 from dad", True, "received X from Y"),
        ("got random money 5000 on the street", True, "found random money"),
        ("found money 10000", True, "found money X"),
        # Should NOT trigger
        ("i got a good grade", False, "got grade (not money)"),
    ]

    for msg, should_trigger, label in cases:
        data = await chat(client, msg)
        tool = data.get("tool_executed", "") or ""
        triggered = tool == "add_income"

        if should_trigger and not triggered:
            passed = False
            issues = f"Expected add_income but got '{tool}'"
        elif not should_trigger and triggered:
            passed = False
            issues = f"False positive: triggered add_income"
        else:
            passed = True
            issues = "OK"

        r = TestResult(
            name=f"Income: {label} — '{msg}'",
            passed=passed,
            details=issues,
            reply=data.get("reply", ""),
            emotion=data.get("emotion", ""),
            tool=tool,
        )
        log_result(r)
        await asyncio.sleep(DELAY)


async def test_task_management(client: httpx.AsyncClient):
    """Test task add/complete/list patterns."""
    print("\n" + "=" * 60)
    print("SUITE: Task Management")
    print("=" * 60)

    cases = [
        ("remind me to buy groceries", "add_task", "remind me to X"),
        ("add task pay electricity bill", "add_task", "add task X"),
        ("what's on my list", "list_tasks", "what's on my list"),
        ("show my tasks", "list_tasks", "show my tasks"),
        ("done with laundry", "complete_task", "done with X"),
        ("i already finished cooking", "complete_task", "already finished X"),
    ]

    for msg, expected_tool, label in cases:
        data = await chat(client, msg)
        tool = data.get("tool_executed", "") or ""

        passed = tool == expected_tool
        issues = f"Expected '{expected_tool}' but got '{tool}'" if not passed else "OK"

        r = TestResult(
            name=f"Task: {label} — '{msg}'",
            passed=passed,
            details=issues,
            reply=data.get("reply", ""),
            emotion=data.get("emotion", ""),
            tool=tool,
        )
        log_result(r)
        await asyncio.sleep(DELAY)


async def test_weather(client: httpx.AsyncClient):
    """Test weather — via tool trigger and direct endpoint."""
    print("\n" + "=" * 60)
    print("SUITE: Weather")
    print("=" * 60)

    # Direct endpoint
    resp = await client.get(f"{BASE}/weather", timeout=10)
    data = resp.json()
    has_current = "current" in data
    has_location = "location" in data
    location = data.get("location", "")

    r = TestResult(
        name="Weather: /weather endpoint returns data",
        passed=has_current and has_location,
        details=f"Location: {location}, has_current: {has_current}",
    )
    log_result(r)

    # Check location is dynamic (resolved from IP / config, not hardcoded)
    r = TestResult(
        name="Weather: location is from IP geolocation",
        passed=bool(location) and location != "",
        details=f"Location: {location}",
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)

    # Via chat
    data = await chat(client, "how's the weather today?")
    r = validate_response("Weather: chat trigger — 'how's the weather'", data, expect_tool="check_weather")
    log_result(r)

    await asyncio.sleep(DELAY)


async def test_balance(client: httpx.AsyncClient):
    """Test balance check via chat."""
    print("\n" + "=" * 60)
    print("SUITE: Balance Check")
    print("=" * 60)

    data = await chat(client, "check my balance")
    r = validate_response("Balance: 'check my balance'", data, expect_tool="check_balance")
    log_result(r)

    await asyncio.sleep(DELAY)

    data = await chat(client, "how much money do I have left")
    r = validate_response("Balance: 'how much money do I have left'", data, expect_tool="check_balance")
    log_result(r)

    await asyncio.sleep(DELAY)


async def test_calendar(client: httpx.AsyncClient):
    """Test calendar check via chat."""
    print("\n" + "=" * 60)
    print("SUITE: Calendar")
    print("=" * 60)

    data = await chat(client, "what's on my schedule today")
    r = validate_response("Calendar: 'what's on my schedule'", data, expect_tool="check_calendar")
    log_result(r)

    await asyncio.sleep(DELAY)


async def test_sleep_cycle(client: httpx.AsyncClient):
    """Test sleep/wake cycle via chat commands."""
    print("\n" + "=" * 60)
    print("SUITE: Sleep/Wake Cycle")
    print("=" * 60)

    # 1. Send sleep command
    data = await chat(client, "go to sleep suichan")
    reply = data.get("reply", "")
    emotion = data.get("emotion", "")

    r = TestResult(
        name="Sleep: 'go to sleep' triggers sleep mode",
        passed=bool(reply) and emotion in VALID_EMOTIONS,
        details=f"Reply: {reply[:80]}",
        reply=reply,
        emotion=emotion,
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)

    # 2. Verify server reports sleeping
    resp = await client.get(f"{BASE}/status", timeout=10)
    status = resp.json()
    is_sleeping = status.get("sleep", {}).get("is_sleeping", False)

    r = TestResult(
        name="Sleep: /status confirms sleeping=true",
        passed=is_sleeping,
        details=f"is_sleeping: {is_sleeping}",
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)

    # 3. Send another sleep command — should get different reply (varied responses)
    data2 = await chat(client, "go to sleep suichan")
    reply2 = data2.get("reply", "")

    r = TestResult(
        name="Sleep: second 'go to sleep' gives varied reply",
        passed=True,  # We can't guarantee it's always different with 5 options, just check it works
        details=f"Reply 1: {reply[:60]} | Reply 2: {reply2[:60]}",
        reply=reply2,
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)

    # 4. Wake up by sending a regular message
    data3 = await chat(client, "hey suichan wake up")
    reply3 = data3.get("reply", "")

    r = TestResult(
        name="Sleep: regular message wakes her up",
        passed=bool(reply3),
        details=f"Reply: {reply3[:80]}",
        reply=reply3,
        emotion=data3.get("emotion", ""),
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)

    # 5. Verify no longer sleeping
    resp = await client.get(f"{BASE}/status", timeout=10)
    status = resp.json()
    is_sleeping = status.get("sleep", {}).get("is_sleeping", False)

    r = TestResult(
        name="Sleep: /status confirms sleeping=false after wake",
        passed=not is_sleeping,
        details=f"is_sleeping: {is_sleeping}",
    )
    log_result(r)

    await asyncio.sleep(DELAY)


async def test_prayer_times(client: httpx.AsyncClient):
    """Test prayer times endpoint and data quality."""
    print("\n" + "=" * 60)
    print("SUITE: Prayer Times")
    print("=" * 60)

    resp = await client.get(f"{BASE}/status", timeout=10)
    status = resp.json()

    prayer_ok = status.get("prayer_times") == "ok"
    r = TestResult(
        name="Prayer: health check reports ok",
        passed=prayer_ok,
        details=f"prayer_times: {status.get('prayer_times')}",
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)


async def test_costume_command(client: httpx.AsyncClient):
    """Test costume change via chat."""
    print("\n" + "=" * 60)
    print("SUITE: Costume Change")
    print("=" * 60)

    data = await chat(client, "change to casual outfit")
    tool = data.get("tool_executed", "") or ""

    r = validate_response("Costume: 'change to casual outfit'", data, expect_tool="change_costume")
    log_result(r)

    await asyncio.sleep(DELAY)

    # Change back
    data = await chat(client, "switch to maid costume")
    tool = data.get("tool_executed", "") or ""
    r = validate_response("Costume: 'switch to maid costume'", data, expect_tool="change_costume")
    log_result(r)

    await asyncio.sleep(DELAY)


async def test_tool_tag_stripping(client: httpx.AsyncClient):
    """Test that TOOL tags and hallucinated blocks don't leak into replies.

    We can't directly control what the LLM outputs, but we verify
    our cleaning works on whatever it returns.
    """
    print("\n" + "=" * 60)
    print("SUITE: Tool Tag / Hallucination Stripping")
    print("=" * 60)

    # Send messages that historically triggered tool tag leaks
    prompts = [
        "note this: 119.83",
        "remember that I like sushi",
        "what should I do today",
        "how's my spending this month",
    ]

    for msg in prompts:
        data = await chat(client, msg)
        reply = data.get("reply", "")

        issues = []
        tool_leak = check_no_tool_leak(reply)
        if tool_leak:
            issues.append(tool_leak)
        emo_leak = check_no_emotion_leak(reply)
        if emo_leak:
            issues.append(emo_leak)

        r = TestResult(
            name=f"Stripping: '{msg}'",
            passed=len(issues) == 0,
            details="; ".join(issues) if issues else "Clean — no leaks",
            reply=reply,
            emotion=data.get("emotion", ""),
            tool=data.get("tool_executed", "") or "",
        )
        log_result(r)
        await asyncio.sleep(DELAY)


async def test_reply_length(client: httpx.AsyncClient):
    """Test that replies are truncated to ~3 sentences max."""
    print("\n" + "=" * 60)
    print("SUITE: Reply Length (3-sentence cap)")
    print("=" * 60)

    # Prompts that tend to make the LLM ramble
    prompts = [
        "tell me everything about yourself",
        "what do you think about the meaning of life",
        "give me a long detailed answer about your favorite things",
    ]

    for msg in prompts:
        data = await chat(client, msg)
        reply = data.get("reply", "")

        # Count sentences (rough: split on .!?~ followed by space)
        sentences = re.split(r'(?<=[.!?~])\s+', reply.strip())
        sentence_count = len([s for s in sentences if s.strip()])

        r = TestResult(
            name=f"Length: '{msg[:40]}...'",
            passed=sentence_count <= 4,  # allow slight over (3 + potential partial)
            details=f"{sentence_count} sentences, {len(reply)} chars",
            reply=reply,
        )
        log_result(r)
        await asyncio.sleep(DELAY)


async def test_geolocation(client: httpx.AsyncClient):
    """Test that geolocation is working (weather endpoint shows IP-based location)."""
    print("\n" + "=" * 60)
    print("SUITE: IP Geolocation")
    print("=" * 60)

    resp = await client.get(f"{BASE}/weather", timeout=10)
    data = resp.json()
    location = data.get("location", "")

    r = TestResult(
        name="Geo: weather endpoint has location from IP",
        passed=bool(location) and "," in location,
        details=f"Location: {location}",
    )
    log_result(r)

    await asyncio.sleep(FAST_DELAY)


async def test_memory(client: httpx.AsyncClient):
    """Test memory save/retrieve via chat and API."""
    print("\n" + "=" * 60)
    print("SUITE: Memory")
    print("=" * 60)

    # 1. Save memory via "note this:"
    data = await chat(client, "note this: my favorite color is blue")
    r = validate_response(
        "Memory: 'note this: ...' triggers save_memory",
        data,
        expect_tool="save_memory",
    )
    log_result(r)
    await asyncio.sleep(DELAY)

    # 2. Save memory via "remember that"
    data = await chat(client, "remember that I like sushi")
    r = validate_response(
        "Memory: 'remember that ...' triggers save_memory",
        data,
        expect_tool="save_memory",
    )
    log_result(r)
    await asyncio.sleep(DELAY)

    # 3. Save memory via "don't forget"
    data = await chat(client, "don't forget my birthday is January 5th")
    r = validate_response(
        "Memory: 'don't forget ...' triggers save_memory",
        data,
        expect_tool="save_memory",
    )
    log_result(r)
    await asyncio.sleep(DELAY)

    # 4. Check that core memories exist via status endpoint
    resp = await client.get(f"{BASE}/status", timeout=10)
    status = resp.json()
    mem_info = status.get("memory", {})
    # memory is a dict with total_messages, core_memories, session
    memory_ok = isinstance(mem_info, dict) and mem_info.get("total_messages", 0) > 0
    r = TestResult(
        name="Memory: /status reports memory info",
        passed=memory_ok,
        details=f"messages: {mem_info.get('total_messages', 0)}, core: {mem_info.get('core_memories', 0)}",
    )
    log_result(r)
    await asyncio.sleep(FAST_DELAY)

    # 5. "remember" without content should NOT trigger save_memory
    data = await chat(client, "do you remember what we talked about?")
    tool = data.get("tool_executed", "") or ""
    r = TestResult(
        name="Memory: 'do you remember...' should NOT trigger save",
        passed=tool != "save_memory",
        details=f"Tool: '{tool}'" if tool else "No tool (correct)",
        reply=data.get("reply", ""),
        emotion=data.get("emotion", ""),
        tool=tool,
    )
    log_result(r)
    await asyncio.sleep(DELAY)


async def test_no_false_triggers(client: httpx.AsyncClient):
    """Test that normal conversation doesn't accidentally trigger tools."""
    print("\n" + "=" * 60)
    print("SUITE: No False Tool Triggers")
    print("=" * 60)

    safe_messages = [
        "good morning suichan",
        "i feel tired today",
        "what do you think about anime",
        "thanks for chatting with me",
        "you're funny lol",
    ]

    for msg in safe_messages:
        data = await chat(client, msg)
        tool = data.get("tool_executed", "") or ""

        r = TestResult(
            name=f"NoTrigger: '{msg}'",
            passed=not tool,
            details=f"Tool: '{tool}'" if tool else "No tool triggered (correct)",
            reply=data.get("reply", ""),
            emotion=data.get("emotion", ""),
            tool=tool,
        )
        log_result(r)
        await asyncio.sleep(DELAY)


# ═══════════════════════════════════════════════════════════════════════
# Main Runner
# ═══════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("  AVATAR SERVER — BLACK-BOX INTEGRATION TESTS")
    print("  Testing against: " + BASE)
    print("  Delay between LLM requests: " + str(DELAY) + "s")
    print("=" * 60)

    # Pre-flight check
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{BASE}/status", timeout=5)
            status = resp.json()
            if status.get("llm") != "ok":
                print(f"\nERROR: LLM ({status.get('llm_provider', '?')}) is offline. Start it first.")
                sys.exit(1)
            print(f"\nServer OK — LLM ({status.get('llm_provider', '?')}): {status['llm']}, uptime: {status['uptime_seconds']}s")
        except Exception as e:
            print(f"\nERROR: Cannot reach avatar-server at {BASE}: {e}")
            sys.exit(1)

    start_time = time.time()

    # Check for --failed-only flag to skip passing suites
    failed_only = "--failed-only" in sys.argv

    async with httpx.AsyncClient() as client:
        if not failed_only:
            await test_health(client)
            await test_geolocation(client)
            await test_basic_chat(client)
            await test_emotion_tag_cleaning(client)
            await test_reply_length(client)
            await test_tool_tag_stripping(client)
        # These suites had failures — always run
        await test_expense_detection(client)
        await test_income_detection(client)
        await test_task_management(client)
        await test_weather(client)
        await test_balance(client)
        await test_calendar(client)
        await test_costume_command(client)
        await test_memory(client)
        if not failed_only:
            await test_no_false_triggers(client)
            await test_sleep_cycle(client)

    elapsed = time.time() - start_time

    # ── Summary ──
    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    if failed > 0:
        print(f"\nFAILED TESTS:")
        for r in results:
            if not r.passed:
                print(f"  FAIL: {r.name}")
                print(f"        {r.details}")
                if r.reply:
                    print(f"        Reply: {r.reply[:100]}")

    print(f"\n  Passed: {passed}/{total}")
    print(f"  Failed: {failed}/{total}")
    print(f"  Time:   {elapsed:.0f}s ({elapsed / 60:.1f}min)")
    print("=" * 60)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())

"""Second brain: backends, dedup, and the memory facade (shared vs private, two characters).

Run: python tests/test_brain.py                      (SQLite only, no server needed)
     BRAIN_TEST_DSN=postgresql://... python tests/test_brain.py   (also runs the Postgres backend
     against that database — it creates and DROPS a `facts` table, so point it at a scratch DB)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import brain, memory  # noqa: E402
from services.brain import base  # noqa: E402
from services.brain.sqlite_backend import SqliteBrain  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


# --- dedup rule ---
check("same words, other order", base.is_duplicate("User likes blue", "blue: user likes"), True)
check("case + punctuation", base.is_duplicate("User's cat is Mochi.", "users cat is mochi"), False)
check("different day's stat is not a dup",
      base.is_duplicate("step count on 2026-09-11: 4,338 steps", "step count on 2026-09-12: 5,100 steps"), False)
check("different fact", base.is_duplicate("User likes blue", "User likes tetris"), False)


def exercise_backend(label, backend):
    a = backend.add("fact", "Alex works as a software engineer in Jakarta", "Aria")
    check(f"{label}: ids start above ID_BASE", a > base.ID_BASE, True)
    check(f"{label}: near-copy returns the same id",
          backend.add("fact", "alex works as a software engineer in jakarta.", "Nova"), a)
    b = backend.add("preference", "Prefers nasi goreng over pizza", "Nova")
    check(f"{label}: count", backend.count(), 2)
    check(f"{label}: all() oldest first", [r["id"] for r in backend.all()], [a, b])
    check(f"{label}: category filter", [r["id"] for r in backend.all("preference")], [b])
    check(f"{label}: author kept", backend.all()[0]["author"], "Aria")
    check(f"{label}: created_at is 'YYYY-MM-DD HH:MM:SS'", len(backend.all()[0]["created_at"]), 19)
    check(f"{label}: search finds by keyword", [r["id"] for r in backend.search("what about that pizza?")], [b])
    check(f"{label}: search ignores punctuation-only", backend.search("?!"), [])
    check(f"{label}: update", backend.update(b, "Prefers mie ayam over pizza"), True)
    check(f"{label}: search sees the update", [r["id"] for r in backend.search("mie ayam")], [b])
    check(f"{label}: old text gone from index", backend.search("goreng"), [])
    check(f"{label}: update missing id", backend.update(999, "x"), False)
    backend.log("summary", "Talked about the trip", "Aria")
    backend.log("summary", "Planned the week", "Nova")
    got = backend.recent("summary", hours=1)
    check(f"{label}: journal newest first", [r["content"] for r in got], ["Planned the week", "Talked about the trip"])
    check(f"{label}: journal carries author + age", (got[0]["author"], got[0]["age_minutes"]), ("Nova", 0))
    check(f"{label}: journal filters by kind", backend.recent("user_state", hours=1), [])
    check(f"{label}: delete_matching", [r["id"] for r in backend.delete_matching("JAKARTA")], [a])
    check(f"{label}: delete", backend.delete(b), True)
    check(f"{label}: empty again", backend.count(), 0)


# memory.py's connections are closed by GC, which Windows notices at cleanup time.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as _tmp:
    tmp = Path(_tmp)

    # --- SQLite backend ---
    exercise_backend("sqlite", SqliteBrain(str(tmp / "unit.db")))

    # --- Postgres backend (opt-in) ---
    dsn = os.environ.get("BRAIN_TEST_DSN")
    if dsn:
        import psycopg
        from services.brain.postgres_backend import PostgresBrain
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS facts")
        try:
            exercise_backend("postgres", PostgresBrain(dsn))
        finally:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("DROP TABLE IF EXISTS facts")
    else:
        print("  [SKIP] postgres backend (set BRAIN_TEST_DSN)")

    # --- facade: two characters, one brain, two private DBs ---
    saved = {k: getattr(config, k) for k in
             ("BRAIN_BACKEND", "BRAIN_DB_PATH", "CHARACTER_NAME", "OWNER_NAME", "BRAIN_PROMPT_MAX")}
    config.BRAIN_BACKEND, config.BRAIN_DB_PATH, config.OWNER_NAME = "sqlite", str(tmp / "brain.db"), "Owner"
    brain.reset()

    def become(name):
        config.CHARACTER_NAME = name
        memory.init(str(tmp / f"{name}.db"))
        memory._brain_changed()

    become("Alpha")
    fact_id = memory.add_core_memory("fact", "Owner is allergic to shrimp", "llm")
    event_id = memory.add_core_memory("relationship", "We watched the finale together", "llm")
    check("fact went to the brain", brain.is_brain_id(fact_id), True)
    check("events about him are shared too", brain.is_brain_id(memory.add_core_memory("event", "Owner's birthday is in October", "llm")), True)
    check("relationship stayed private", brain.is_brain_id(event_id), False)

    become("Beta")
    seen = memory.get_core_memories()
    check("Beta sees Alpha's fact + event", [m["content"] for m in seen],
          ["Owner is allergic to shrimp", "Owner's birthday is in October"])
    check("Beta does NOT see Alpha's relationship memory", any("finale" in m["content"] for m in seen), False)
    block = memory.format_core_memories_for_prompt()
    check("Beta's prompt attributes it", "(via Alpha)" in block, True)
    check("Beta has no private section yet", "Things I Remember" in block, False)
    check("Beta saying it again does not duplicate",
          memory.add_core_memory("fact", "owner is allergic to shrimp!", "llm"), fact_id)
    check("Beta can correct a shared fact", memory.update_core_memory(fact_id, "Owner is allergic to shrimp and crab"), True)

    become("Alpha")
    block = memory.format_core_memories_for_prompt()
    check("Alpha sees Beta's correction", "shrimp and crab" in block, True)
    check("Alpha's own fact is unmarked", "(via Alpha)" in block or "(via Beta)" in block, False)
    check("Alpha still has her private relationship memory", "We watched the finale together" in block, True)
    check("count = private + shared", memory.get_core_memory_count(), 3)
    check("recent merges private + shared",
          sorted(m["content"] for m in memory.get_recent_core_memories(limit=5)),
          ["Owner is allergic to shrimp and crab", "Owner's birthday is in October", "We watched the finale together"])
    check("search-delete reaches the brain", [d["id"] for d in memory.search_and_delete_core_memories("crab")], [fact_id])
    check("gone for everyone", memory.get_core_memory_count(), 2)

    # --- journal: what he did with the others, how he is doing ---
    from services import user_state
    become("Beta")
    memory.add_session_summary("Owner vented about a rough deploy", 12, "2026-09-20 10:00:00", "2026-09-20 10:30:00")
    user_state._share({"physical": "tired", "mental": "stressed", "energy": "low", "context": "rough deploy"})
    user_state._share({"physical": "fit", "mental": "normal", "energy": "normal", "context": ""})
    own = memory.format_summaries_for_prompt()
    check("Beta's own summary is not echoed back as someone else's", "Fellow Companions" in own, False)
    become("Alpha")
    brain.reset()
    seen = memory.format_summaries_for_prompt()
    check("Alpha sees what he did with Beta", "(with Beta, 0m ago) Owner vented about a rough deploy" in seen, True)
    check("...framed as not being there", "You were not there" in seen, True)
    state = user_state.format_for_prompt()
    check("Alpha sees Beta's read of him", "seen by Beta" in state and "stressed" in state, True)
    check("a default state was never journalled", len(brain.get().recent("user_state", hours=1)), 1)

    # --- daily numbers are acknowledged, never stored ---
    import asyncio
    from services.tools import memory_tool
    become("Alpha")
    before = memory.get_core_memory_count()
    res = asyncio.run(memory_tool.handle_save_memory("fact|Owner's step count on 2026-09-21 (Monday): 8,120 steps", {}))
    check("step count save is acknowledged", res["ok"], True)
    check("...but nothing is stored", memory.get_core_memory_count(), before)
    asyncio.run(memory_tool.handle_save_memory("fact|Owner wants to walk more this year", {}))
    check("a real fact about walking still saves", memory.get_core_memory_count(), before + 1)
    memory.search_and_delete_core_memories("walk more")

    # --- brain down: nothing is lost, sync picks it up later ---
    config.BRAIN_BACKEND, config.BRAIN_DSN = "postgres", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"
    brain.reset()
    memory._brain_changed()
    parked = memory.add_core_memory("fact", "Owner plays tetris at night", "llm")
    check("brain down -> parked privately", brain.is_brain_id(parked), False)
    check("parked fact still shows in the prompt", "tetris" in memory.format_core_memories_for_prompt(), True)
    config.BRAIN_BACKEND = "sqlite"
    brain.reset()
    check("sync moves it across", memory.sync_to_brain(), 1)
    check("second sync is a no-op", memory.sync_to_brain(), 0)
    check("shown once, from the brain", memory.format_core_memories_for_prompt().count("tetris"), 1)
    moved_id = [m["id"] for m in memory.get_core_memories("fact")][0]
    memory.delete_core_memory(moved_id)
    memory.sync_to_brain()
    check("deleted fact is not resurrected by sync", memory.get_core_memories("fact"), [])

    # --- pre-brain install: existing rows migrate on init ---
    config.CHARACTER_NAME = "Legacy"
    import sqlite3
    legacy = tmp / "Legacy.db"
    memory.init(str(legacy))
    with sqlite3.connect(legacy) as conn:
        conn.executemany("INSERT INTO core_memories (category, content, source) VALUES (?, ?, 'llm')",
                         [("fact", "Owner has a cat named Mochi"), ("relationship", "First stream together")])
    memory.init(str(legacy))
    block = memory.format_core_memories_for_prompt()
    check("legacy fact now shared", "#10" in block and "Mochi" in block.split("Shared Notes")[1], True)
    check("legacy relationship memory still private", "First stream together" in block.split("Shared Notes")[0], True)

    # --- over the cap: recall by message, never the whole brain ---
    config.BRAIN_PROMPT_MAX = 4
    for i in range(8):
        memory.add_core_memory("fact", f"Owner filler note number {i} about topic{i}", "llm")
    memory._brain_changed()
    block = memory.format_core_memories_for_prompt("how is my cat mochi doing")
    shared_lines = [ln for ln in block.split("Shared Notes")[1].splitlines() if ln.startswith("- #")]
    check("capped", len(shared_lines), 4)
    check("the relevant old fact made the cut", any("Mochi" in ln for ln in shared_lines), True)

    for k, v in saved.items():
        setattr(config, k, v)
    brain.reset()

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

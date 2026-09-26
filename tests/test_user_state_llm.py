"""user_state derives on any LLM provider: Claude CLI (haiku, unchanged request) or an
OpenAI-compatible model, and leaves the state alone when every rung fails.

Run: python tests/test_user_state_llm.py   (no server, no network)
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
tmp = Path(_tmp.name)
config.USER_STATE_PATH = str(tmp / "user_state.json")
config.MEMORY_DB_PATH = str(tmp / "memory.db")
config.BRAIN_BACKEND, config.BRAIN_DB_PATH = "sqlite", str(tmp / "brain.db")
config.LLM_FALLBACK_1 = config.LLM_FALLBACK_2 = ""  # no fallback rungs: only the provider under test

from services import llm_json, memory, user_state  # noqa: E402

memory.init(config.MEMORY_DB_PATH)

failures = 0


def check(name, got, want):
    global failures
    if got == want:
        print(f"  [PASS] {name}")
    else:
        failures += 1
        print(f"  [FAIL] {name}: got {got!r} want {want!r}")


REPLY = '{"physical": "Tired", "mental": "busy", "energy": "low", "context": "long deploy day"}'
claude_calls, openai_calls = [], []


async def fake_claude(**kwargs):
    claude_calls.append(kwargs)
    return REPLY


class FakeOpenAI:
    def __init__(self, base_url, api_key, timeout):
        self.base_url = base_url
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        openai_calls.append({"base_url": self.base_url, **kwargs})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=REPLY))])


llm_json.query_claude_cli = fake_claude
llm_json.OpenAI = FakeOpenAI

# --- Claude: the same haiku request as before, a lone user message ---
config.LLM_PROVIDER = "claude"
state = asyncio.run(user_state.derive_now())
check("claude: state derived", (state["physical"], state["mental"], state["energy"]), ("tired", "busy", "low"))
check("claude: context kept as written", state["context"], "long deploy day")
call = claude_calls[-1]
check("claude: haiku, low effort, 45s", (call["model"], call["effort"], call["timeout"]), ("haiku", "low", 45))
check("claude: only a user message (no system)", [m["role"] for m in call["messages"]], ["user"])
check("claude: no OpenAI call", openai_calls, [])

# --- OpenAI-compatible (OpenAI, Grok via base URL, ...) ---
config.LLM_PROVIDER, config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY = (
    "openai", "https://api.x.ai/v1", "grok-test", "k")
claude_before = len(claude_calls)
state = asyncio.run(user_state.derive_now())
check("openai: state derived", state["energy"], "low")
check("openai: configured endpoint + model used", (openai_calls[-1]["base_url"], openai_calls[-1]["model"]),
      ("https://api.x.ai/v1", "grok-test"))
check("openai: Claude CLI never called", len(claude_calls), claude_before)

# --- every rung fails: state untouched ---
before = user_state.get_state()


class DeadOpenAI(FakeOpenAI):
    def _create(self, **kwargs):
        raise RuntimeError("provider down")


llm_json.OpenAI = DeadOpenAI
check("all rungs fail -> None", asyncio.run(user_state.derive_now()), None)
check("all rungs fail -> state unchanged", user_state.get_state(), before)

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)

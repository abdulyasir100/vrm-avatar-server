"""Access tiers — how much the companion may do, per trust context.

One setting (config.ACCESS_LEVEL, `/set access_level ...`) and two tables. A tier is
a CEILING: finer switches (tool_call_mode, shell_enabled) still apply underneath it.

    effective tier = min(ACCESS_LEVEL, ceiling of the turn's context)

Unattended turns are capped low no matter what ACCESS_LEVEL says, because their
prompts embed untrusted text (headlines, task and event titles). The one way up is
a plugin scheduled_action that declares `"allow_code_mode": true` in its manifest:
that turn is `elevated` and gets the chat ceiling instead.

The hard floor (services/floors.py) is separate and sits BELOW all of this: it
refuses catastrophic commands even at administrator.
"""

import config
from services import turn_context as tc

# Ascending. Index = rank.
TIERS = ("nothing", "entry", "mid", "administrator")

# capability -> lowest tier that has it
CAPABILITIES = {
    "tools": "entry",          # plugin + main tools
    "shell": "mid",            # run_shell (media-drive sidecar)
    "code": "administrator",   # code mode: Opus + Bash/Write/Edit in the sandbox
}

# trust context -> highest tier it can ever have
CONTEXT_CEILING = {
    tc.CHAT: "administrator",
    tc.IDLE: "entry",
    tc.SCHEDULED: "entry",
    tc.BACKGROUND: "entry",
}


def _rank(tier: str) -> int:
    # An unrecognised value (bad env var) is the safest tier, not a crash.
    return TIERS.index(tier) if tier in TIERS else 0


def effective_tier(turn_class: str = tc.CHAT, elevated: bool = False) -> str:
    ceiling = CONTEXT_CEILING[tc.CHAT] if elevated else CONTEXT_CEILING.get(turn_class, TIERS[0])
    return TIERS[min(_rank(config.ACCESS_LEVEL), _rank(ceiling))]


def allows(capability: str, turn_class: str = tc.CHAT, elevated: bool = False) -> bool:
    return _rank(effective_tier(turn_class, elevated)) >= _rank(CAPABILITIES[capability])


def allows_ctx(capability: str, ctx: dict, elevated: bool = False) -> bool:
    """allows() for the {"context", "user_name"} dict tool handlers receive."""
    return allows(capability, tc.classify(ctx or {}), elevated)

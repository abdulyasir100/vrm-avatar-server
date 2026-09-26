"""Who started this turn — the user, or the companion on her own.

Single source of truth for the trust context of a turn. Every gate that treats
unattended turns differently (services/access.py, run_shell) keys off classify().
"""

CHAT = "chat"              # a person asked, live
IDLE = "idle"              # unprompted talk
SCHEDULED = "scheduled"    # a plugin scheduled_action fired
BACKGROUND = "background"  # reminders, nags, plugin checks/tasks, anything else as "System"

# Chat surfaces that show the reply themselves (their bridge posts what /chat returns). Any other
# context — the tablet, a webhook — gets the reply pushed to Telegram as well.
SELF_DELIVERING_SURFACES = ("telegram", "discord")

# context-string prefix -> class. First match wins; checked before the System fallback.
_CONTEXT_PREFIXES = (
    (("idle", "smart_idle", "memory_idle"), IDLE),
    (("scheduled",), SCHEDULED),
    (("background", "spending_nag"), BACKGROUND),
)


def classify(ctx: dict) -> str:
    """Trust context of a turn, from the {"context", "user_name"} pair llm.chat() receives."""
    context = str(ctx.get("context") or "")
    for prefixes, turn_class in _CONTEXT_PREFIXES:
        if context.startswith(prefixes):
            return turn_class
    if context.endswith("_check") or ctx.get("user_name") == "System":
        return BACKGROUND
    return CHAT


def is_autonomous(ctx: dict) -> bool:
    """True for turns the model started on its own (no user asked)."""
    return classify(ctx) != CHAT

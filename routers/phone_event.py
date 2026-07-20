"""Phone-event ingress — receives push events from avatar-phone.

Currently handles lockout notifications: when avatar-phone hits its PIN
failure threshold, it POSTs here so the character can call the user out via the
normal speech queue. Fire-and-forget on the caller side.
"""

from __future__ import annotations

import logging
import random

from fastapi import APIRouter
from pydantic import BaseModel

from services import background
from services.message_queue import QueueItem, enqueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/phone")


class LockoutEvent(BaseModel):
    attempts: int
    duration_s: int


_LOCKOUT_PROMPTS = [
    "[Inner thought: Someone just botched my phone PIN {attempts} times. "
    "It's locked for {duration_s} seconds now. The user is the only one "
    "near my phone — call them out for snooping. One short message, in "
    "character. Be petty about it.]",
    "[Inner thought: My phone just locked itself after {attempts} wrong "
    "PIN attempts. That's the user trying to guess. Roast them in one "
    "sharp sentence.]",
    "[Inner thought: {attempts} failed PIN attempts on my phone — now "
    "locked for {duration_s}s. React to the snoop. One line, sharp.]",
]


@router.post("/lockout")
async def phone_lockout(ev: LockoutEvent):
    prompt = random.choice(_LOCKOUT_PROMPTS).format(
        attempts=ev.attempts, duration_s=ev.duration_s
    )
    logger.warning(
        f"[phone] lockout event: attempts={ev.attempts}, "
        f"locking for {ev.duration_s}s"
    )
    await enqueue(QueueItem(
        type="idle_talk",
        handler=lambda p=prompt: background._speak(p, "phone_lockout"),
        label=f"phone_lockout_{ev.attempts}",
    ))
    return {"ok": True}

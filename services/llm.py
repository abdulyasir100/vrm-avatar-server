"""LLM client with emotion tag parsing and conversation memory.

Supports any OpenAI-compatible provider: LM Studio, Ollama, OpenAI, Groq.
Set LLM_PROVIDER in .env to switch (default: lmstudio).
"""

import re
import json
import logging
import asyncio
from pathlib import Path
from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError, BadRequestError
from services import memory, tool_router, tool_registry, mood
from services.claude_cli import query_claude_cli, stream_claude_cli, check_claude_cli
import config

logger = logging.getLogger(__name__)

_system_prompt: str = ""


def _interpolate(text: str) -> str:
    """Replace {{TOKENS}} in prompt text with live config values.

    Lets prompt files (system.md, personality.md) stay character-agnostic:
    {{CHARACTER_NAME}}, {{OWNER_NAME}}, {{OWNER_PRONOUN}}, {{TZ_LABEL}}, {{LANGUAGE}}.
    Uses plain token replacement (not str.format) since prompts contain [TOOL:]/[HAPPY].
    """
    if not text:
        return text
    return (text
            .replace("{{CHARACTER_NAME}}", config.CHARACTER_NAME)
            .replace("{{OWNER_NAME}}", config.OWNER_NAME)
            .replace("{{OWNER_PRONOUN}}", config.OWNER_PRONOUN)
            .replace("{{TZ_LABEL}}", config.TZ_LABEL)
            .replace("{{LANGUAGE}}", config.LANGUAGE))


def _load_from_card(card_path: Path) -> str:
    """Build system prompt from a character.json card."""
    card = json.loads(card_path.read_text(encoding="utf-8"))

    # Use card's system_prompt if provided, otherwise fall back to system.md
    explicit = card.get("system_prompt", "").strip()
    if explicit:
        base_prompt = explicit
    else:
        md_path = Path(config.SYSTEM_PROMPT_PATH)
        base_prompt = md_path.read_text(encoding="utf-8").strip() if md_path.exists() else ""

    # Prepend structured metadata from card fields
    parts = []
    if card.get("name"):
        parts.append(f"Character: {card['name']}")
    if card.get("description"):
        parts.append(f"Description: {card['description']}")
    if card.get("personality"):
        parts.append(f"Personality: {card['personality']}")
    if card.get("scenario"):
        parts.append(f"Scenario: {card['scenario']}")

    preamble = "\n".join(parts)
    if preamble and base_prompt:
        full = preamble + "\n\n" + base_prompt
    else:
        full = preamble or base_prompt

    logger.info(f"Loaded character card: {card.get('name', '?')} from {card_path} ({len(full)} chars)")
    return _interpolate(full)


def load_system_prompt() -> str:
    """Load system prompt from character.json (if present), else prompts/system.md."""
    global _system_prompt

    # Try character card first
    card_path = Path(config.CHARACTER_CARD_PATH)
    if card_path.exists():
        try:
            _system_prompt = _load_from_card(card_path)
            return _system_prompt
        except Exception as e:
            logger.warning(f"Failed to load character card {card_path}: {e}, falling back to system.md")

    # Load personality + system prompt (split files)
    parts = []
    personality_path = Path("prompts/personality.md")
    system_path = Path(config.SYSTEM_PROMPT_PATH)

    if personality_path.exists():
        parts.append(personality_path.read_text(encoding="utf-8").strip())
        logger.info(f"Loaded personality from {personality_path}")
    if system_path.exists():
        parts.append(system_path.read_text(encoding="utf-8").strip())
        logger.info(f"Loaded system rules from {system_path}")

    if parts:
        _system_prompt = _interpolate("\n\n".join(parts))
        logger.info(f"System prompt: {len(_system_prompt)} chars (personality + system rules)")
    else:
        _system_prompt = "You are a helpful assistant. Prefix every reply with an emotion tag like [HAPPY] on its own line."
        logger.warning("No prompt files found, using fallback")
    return _system_prompt


def get_system_prompt() -> str:
    """Return the cached system prompt."""
    if not _system_prompt:
        load_system_prompt()
    return _system_prompt


# --- avatar-phone context injection -----------------------------------------

import os as _os
import time as _time
import urllib.request as _urlreq

# avatar-phone is an optional external service. Leave AVATAR_PHONE_URL unset to
# disable the integration (no fetch, no startup warnings).
_PHONE_BASE = _os.environ.get("AVATAR_PHONE_URL", "")
_phone_ctx_cache: tuple[float, str] | None = None
_PHONE_CTX_TTL = 60.0  # seconds


def get_phone_context() -> str:
    """Fetch the current phone-state summary from avatar-phone for system-
    prompt injection. Cached for 60s on success. Disabled when AVATAR_PHONE_URL
    is unset (returns "").

    On failure: return last-known-good value (may be stale) without caching,
    so the next call retries instead of leaving the character phone-blind for 60s.
    """
    global _phone_ctx_cache
    if not _PHONE_BASE:
        return ""
    now = _time.time()
    if _phone_ctx_cache and (now - _phone_ctx_cache[0]) < _PHONE_CTX_TTL:
        return _phone_ctx_cache[1]
    try:
        with _urlreq.urlopen(f"{_PHONE_BASE}/context/summary", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            prompt = data.get("prompt", "")
        _phone_ctx_cache = (now, prompt)
        return prompt
    except Exception as e:
        logger.warning(f"[llm] avatar-phone context fetch failed: {e!r}")
        return _phone_ctx_cache[1] if _phone_ctx_cache else ""


async def prime_phone_context() -> None:
    """Best-effort: warm the phone-context cache at startup so the first
    chat doesn't fire on an empty cache and risk hallucinating phone state.
    """
    if not _PHONE_BASE:
        return
    await asyncio.sleep(2)
    for attempt in range(3):
        try:
            ctx = await asyncio.to_thread(get_phone_context)
            if ctx:
                logger.info(f"[startup] phone context primed ({len(ctx)} chars)")
                return
        except Exception as e:
            logger.warning(f"[startup] phone context prime attempt {attempt+1} failed: {e!r}")
        await asyncio.sleep(5)
    logger.warning("[startup] phone context not primed after 3 attempts; first chat may be phone-blind")


def _is_cloud_provider() -> bool:
    """Return True if using a cloud LLM provider (not local)."""
    return config.LLM_PROVIDER not in ("lmstudio", "ollama", "claude")


def _get_client() -> OpenAI:
    """Create an OpenAI-compatible client for the configured LLM provider."""
    return OpenAI(
        base_url=config.get_llm_base_url(),
        api_key=config.get_llm_api_key() or "no-key",
        timeout=config.LLM_TIMEOUT,
        max_retries=2 if _is_cloud_provider() else 0,
    )


def _truncate_sentences(text: str, max_sentences: int = 3) -> str:
    """Keep only the first N sentences. Prevents 8B model rambling."""
    parts = re.split(r'(?<=[.!?~])\s+', text.strip())
    if len(parts) <= max_sentences:
        return text.strip()
    return " ".join(parts[:max_sentences]).strip()


def parse_tool_call(text: str) -> tuple[str | None, str | None, str]:
    """
    Parse tool call tag from LLM response (anywhere in text, with or without brackets).

    Handles all LLM formatting variants:
      [TOOL:change_costume:casual]   — bracketed (intended format)
      TOOL:change_costume:casual     — no brackets (LLM inconsistency)
      Anywhere in the text, not just at the start.
    """
    match = re.search(r"\[TOOL:([a-z_]+)(?::([^\]]*))?\]", text)
    if not match:
        match = re.search(r"TOOL:([a-z_]+):([^\s\[]*)", text)

    if match:
        tool_name = match.group(1).strip()
        tool_arg = (match.group(2) or "").strip().replace("\\n", "")
        remainder = text[:match.start()] + text[match.end():]
        remainder = re.sub(r"\s*\\n\s*", " ", remainder)
        remainder = re.sub(r"\s*\n\s*", " ", remainder)
        remainder = remainder.strip()
        return tool_name, tool_arg, remainder

    return None, None, text


def parse_emotion(text: str) -> tuple[str, str]:
    """
    Parse emotion tag from LLM response.

    Input:  "[HAPPY] That's great to hear!"
    Output: ("HAPPY", "That's great to hear!")

    Input:  "No tag here"
    Output: ("NEUTRAL", "No tag here")

    Scans all bracketed tags in the text, picks the first VALID emotion,
    and strips every bracketed tag from the reply. Handles GPT-OSS's habit
    of emitting [NEUTRAL] [AHAPPY] (double tag) or [AHAPPY] inline mid-text.
    """
    valid = set(config.VALID_EMOTIONS)
    tag_pattern = re.compile(r"\[([A-Z]+)\]")

    first_valid = None
    for m in tag_pattern.finditer(text):
        tag = m.group(1).strip()
        if tag in valid:
            first_valid = tag
            break

    cleaned = tag_pattern.sub("", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if first_valid:
        # THINKING is a valid VRM expression but Haiku leaks chain-of-thought
        # with this tag — remap to NEUTRAL to avoid showing thinking face
        if first_valid == "THINKING":
            first_valid = "NEUTRAL"
        return first_valid, cleaned

    # Bare (no-bracket) emotion prefix like "HAPPY text here"
    emotions_joined = "|".join(config.VALID_EMOTIONS)
    bare_match = re.match(r"^\s*(" + emotions_joined + r")\s+(.+)", cleaned or text, re.DOTALL)
    if bare_match:
        return bare_match.group(1), bare_match.group(2).strip()

    return config.DEFAULT_EMOTION, cleaned or text.strip()


def _parse_text_function_call(text: str) -> tuple[str | None, str | None, str]:
    """Parse <function=name{args}</function> format that Groq/Llama sometimes outputs as text.

    Returns: (tool_name, tool_arg, remainder_text)
    """
    # Match: <function=tool_name>{"arg": "value"}</function> or <function=tool_name {"arg": "value"}></function>
    # Groq uses closing > after name, Mistral omits it
    match = re.search(r"<function=([a-z_]+)>?\s*([^<]*?)\s*>?\s*</function>", text)
    if match:
        tool_name = match.group(1)
        args_str = match.group(2).strip() if match.group(2) else ""
        tool_arg = ""
        if args_str:
            try:
                args = json.loads(args_str)
                tool_arg = args.get("arg", "")
            except json.JSONDecodeError:
                tool_arg = args_str
        remainder = text[:match.start()] + text[match.end():]
        remainder = remainder.strip()
        return tool_name, tool_arg or "", remainder
    return None, None, text


def parse_bilingual(text: str) -> tuple[str, str | None]:
    """Parse bilingual response (en/jp lines) when JP mode is active.

    Handles both multi-line and single-line formats:
    Input:  "en: Hey dumbass\njp: バカじゃん"
    Input:  "en: Hey dumbass jp: バカじゃん"
    Output: ("Hey dumbass", "バカじゃん")
    """
    import re
    en_text = None
    jp_text = None

    # Try multi-line first
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("en:"):
            en_text = stripped[3:].strip()
        elif stripped.lower().startswith("jp:"):
            jp_text = stripped[3:].strip()

    # If multi-line didn't work, try single-line: "[optional leading text] en: ... jp: ..."
    # Use re.search (not re.match) so leading prefix text before "en:" is discarded,
    # otherwise the LLM occasionally leaks a preamble and the whole thing falls through
    # to "not bilingual", causing TTS to speak the raw en:/jp: markers in JP mode.
    if not (en_text and jp_text):
        match = re.search(r'(?i)\ben:\s*(.+?)\s+\bjp:\s*(.+)', text.strip(), flags=re.DOTALL)
        if match:
            en_text = match.group(1).strip()
            jp_text = match.group(2).strip()

    if en_text and jp_text:
        return en_text, jp_text

    # Not bilingual format — return original text, no JP
    return text, None


# Self-referential tool-failure excuses spoken as dialogue ("my search tool's being
# difficult", "Skill's not loading on my end") — drop that sentence, keep the rest.
_TOOL_EXCUSE = re.compile(
    r"\b(?:my|our) (?:\w+[- ])?(?:tool|skill|system)s?\b[^.!?]{0,30}?"
    r"(?:difficult|not load|isn'?t load|not work|won'?t work|act(?:ing)? up|glitch|broken|down right now|fail)"
    r"|\b(?:tool|skill)'?s (?:not|isn'?t) (?:load|work|cooperat|respond)",
    re.IGNORECASE,
)


def _strip_thinking_preamble(text: str) -> str:
    """Strip LLM chain-of-thought leaked into reply text.

    Haiku sometimes dumps reasoning before the actual response, e.g.:
    'The user is asking about X. She would Y. Keep it Z. Oh, so you think...'
    Also strips explicit [THINKING]...[/THINKING] blocks.
    """
    # Strip explicit [THINKING]...[/THINKING] blocks with content
    text = re.sub(r'\[THINKING\].*?\[/THINKING\]', '', text, flags=re.IGNORECASE | re.DOTALL)
    # Strip unclosed [THINKING] block (everything from [THINKING] to end, or to first real dialogue)
    text = re.sub(r'\[THINKING\].*$', '', text, flags=re.IGNORECASE | re.DOTALL)
    # Strip orphan tags
    text = re.sub(r'\[/?THINKING\]', '', text, flags=re.IGNORECASE)
    # Strip XML-style <thinking>...</thinking> (closed)
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.IGNORECASE | re.DOTALL)
    # Strip unclosed <thinking> to end
    text = re.sub(r'<thinking>.*$', '', text, flags=re.IGNORECASE | re.DOTALL)
    # Strip orphan </thinking> tags
    text = re.sub(r'</thinking>', '', text, flags=re.IGNORECASE)
    # Strip "As <character>, I'd..." meta-reasoning blocks (common Haiku leak)
    char_names = [re.escape(config.CHARACTER_NAME)] + [re.escape(n) for n in (config.CHARACTER_NICKNAMES or [])]
    char_alt = "|".join(char_names) if char_names else "Assistant"
    text = re.sub(rf'As (?:{char_alt}),?\s+I(?:\'d|\'ll| would| should| need to| want to).*?(?=[.!?])[.!?]\s*', '', text, flags=re.IGNORECASE)
    text = text.strip()
    if not text:
        return text
    # Split into sentences
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    # Drop tool-excuse sentences — unless that would empty the reply entirely
    kept = [p for p in parts if not _TOOL_EXCUSE.search(p)]
    if kept and len(kept) < len(parts):
        parts = kept
        text = " ".join(parts).strip()
    if len(parts) <= 1:
        return text

    # Find the first sentence that looks like actual dialogue (not meta-reasoning)
    # Build character-specific patterns dynamically
    _char_names = [re.escape(n) for n in [config.CHARACTER_NAME] + (config.CHARACTER_NICKNAMES or [])]
    _char_alt = "|".join(_char_names) if _char_names else "Assistant"

    meta_patterns = re.compile(
        r"^(?:The user|I should|I need to|I'll |She would|He would|They would|"
        r"He'?s |She'?s |They'?re |It'?s (?:a |time|about|evening|morning|late)|"
        rf"Keep it|Let me|This is about|(?:{_char_alt})'?s? (?:birthday|would|should)|"
        r"The question|I want to|My response|I'?m going to|"
        r"The (?:message|context|conversation)|Stay in character|"
        r"Respond with|Make sure|One sentence|One emotion|"
        r"This is (?:a |my )|Options:|Don'?t forget|"
        r"Given (?:they|the|that)|Since (?:they|the|it)|"
        r"(?:Perfect|Good|Great) (?:contradiction|opportunity|chance|time)|"
        r"The \w+ tool|I (?:don'?t|can'?t) (?:have|access|use)|"
        r"(?:tool|function|skill|system) (?:isn'?t|is not|limitation|not available)|"
        r"(?:genuine|real|sweet|tender|soft|vulnerable) (?:moment|emotion|feeling)|"
        r"(?:show|express|convey) (?:I |that I |my |some )|"
        r"(?:without being|not being|avoid being) (?:sappy|cheesy|cringe|too |over)|"
        r"Okay so |Right,? so |Hmm,? (?:he|she|they|the user)|"
        rf"As (?:{_char_alt}),? I|I have access to|I (?:can|should) (?:use|set|call)|"
        r"The system (?:context|tells|only)|I (?:don'?t|can'?t) (?:know|tell|see)|"
        r"(?:But|And|So) I should|In character|"
        r"(?:Time to|Need to|Going to|Gonna) (?:be |show |roast|tease|comfort))",
        re.IGNORECASE,
    )

    first_real = 0
    for i, part in enumerate(parts):
        if not meta_patterns.match(part.strip()):
            first_real = i
            break

    if first_real > 0:
        return " ".join(parts[first_real:]).strip()
    return text


def _clean_reply_text(text: str, skip_truncate: bool = False) -> str:
    """Strip stray emotion tags, tool tags, and formatting artifacts from reply text."""
    all_emotion_words = config.VALID_EMOTIONS + ["NORMAL", "RELAXED", "EXCITED", "ANNOYED", "CONFUSED", "SHY", "BORED", "CALM"]
    emotions_pattern = "|".join(all_emotion_words)

    text = re.sub(r"\[(?:" + emotions_pattern + r")\]\s*", "", text)
    text = re.sub(r"(?m)^(?:" + emotions_pattern + r")\s+", "", text)
    text = re.sub(r"\s+(?:" + emotions_pattern + r")(?:\s|$)", " ", text)

    text = re.sub(r"\[TOOL:[^\]]*\]", "", text)
    text = re.sub(r"TOOL:[a-z_]+:[^\s]*", "", text)
    # Clean <function=...> tags that leaked into text (Groq uses >, Mistral omits it)
    text = re.sub(r"<function=[^<]*?>?\s*</function>", "", text)
    text = re.sub(r"(?m)^category:\s+.*$", "", text)
    text = re.sub(r"(?m)^content:\s+.*$", "", text)
    text = re.sub(r"\s*category:\s*\w+\s*content:\s*[^.!?~]*", "", text)
    # Strip leaked conversation turns (model sometimes bleeds "User: ..." into output)
    text = re.sub(r"\s*(?:User|Human|Assistant|System)\s*:.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = text.strip()

    if not skip_truncate:
        text = _truncate_sentences(text, max_sentences=3)

    text = re.sub(r"(?m)^(?:" + emotions_pattern + r")\s+", "", text).strip()

    # Guard against dots-only or empty responses
    if not text or re.fullmatch(r"[.\s…]+", text):
        text = ""

    return text


def _detect_mode(message: str, has_tool_match: bool) -> str:
    """Return 'fast', 'smart', or 'code' based on message content.

    Code-mode checks run BEFORE has_tool_match, so explicit sandbox/dev
    requests override incidental tool trigger matches (e.g. a message that
    mentions 'memory' shouldn't force fast mode if it's really a code task).
    """
    msg = message.lower()

    # Explicit mention of the code sandbox or its projects → code mode, always
    sandbox_pattern = (
        r"\b(?:companion[\s\-_]?sandbox|astral[\s\-_]?idols)\b"
    )
    if re.search(sandbox_pattern, msg):
        return "code"

    # Verb × dev-object patterns
    code_patterns = [
        r"\b(build|code|create|mak\w*|write|develop|invent|design|add|edit|modify|update|implement|refactor|scaffold)\b"
        r".*\b(app|script|tool|bot|page|website|site|program|game|monster\w*|move\w*|species|file|json|config|repo|project|sandbox|plugin|endpoint|function|class|module)\b",
        r"\b(fix|debug|refactor|rewrite)\b.*\b(code|script|bug|error|file|function|test)\b",
        r"\b(deploy|dockerize|dockerfile|restart)\b.*\b(service|server|container|image)\b",
        r"\bgo\s+(?:to|into|work\s+on)\s+\S*(?:sandbox|repo|project|astral|game)\b",
        r"\bwork\s+on\s+(?:the\s+)?(?:game|plugin|service|backend|sandbox)\b",
    ]
    if any(re.search(p, msg) for p in code_patterns):
        return "code"

    if has_tool_match:
        return "fast"

    # Smart mode
    smart_patterns = [
        r"\b(search|look up|find out|google|browse)\b",
        r"\b(what('s| is) (happening|going on|new|trending))\b",
        r"\b(news|latest|recent|update)\b.*\?",
        r"\b(who is|what is|tell me about|explain)\b",
        r"\b(research|analyze|summarize|compare)\b",
    ]
    if any(re.search(p, msg) for p in smart_patterns):
        return "smart"
    if "?" in msg and len(msg.split()) > 20:
        return "smart"

    return "fast"


async def _chat_with_claude_cli(
    messages: list[dict],
    model: str,
    effort: str,
    timeout: int,
    tools: str,
    cwd: str | None = None,
) -> dict | None:
    """Chat via Claude CLI subprocess. Returns standard result dict or None."""
    raw = await query_claude_cli(
        messages=messages,
        model=model,
        effort=effort,
        timeout=timeout,
        allowed_tools=tools,
        cwd=cwd,
    )
    if not raw:
        return None

    # Parse tool call (text-tag mode)
    tool_name, tool_arg, remainder = parse_tool_call(raw)
    if tool_name:
        logger.info(f"[claude_cli] Tool call: {tool_name}({tool_arg})")

    # Parse emotion — skip sentence truncation (Claude handles length well)
    emotion, clean_reply = parse_emotion(remainder)
    clean_reply = _strip_thinking_preamble(clean_reply)
    clean_reply = _clean_reply_text(clean_reply, skip_truncate=True)

    logger.info(f"[claude_cli] Parsed: [{emotion}] {clean_reply[:80]}...")

    return {
        "reply": clean_reply,
        "emotion": emotion,
        "audio_url": None,
        "tool_name": tool_name,
        "tool_arg": tool_arg,
    }


async def _stream_chat_with_claude_cli(
    messages: list[dict],
    model: str,
    effort: str,
    timeout: int,
    tools: str,
    on_sentence: callable = None,
) -> dict | None:
    """Stream Claude CLI response with sentence callbacks for TTS.

    Streams text deltas, accumulates them, and calls on_sentence(text)
    for each complete sentence as it arrives. Returns the same result dict
    as _chat_with_claude_cli() once complete.

    The on_sentence callback receives raw sentence text (before tool/emotion
    parsing). The first sentence will contain the [TOOL:...][EMOTION] prefix —
    the caller should parse and strip it.
    """
    full_text = ""
    sentence_count = 0

    async for event in stream_claude_cli(
        messages=messages,
        model=model,
        effort=effort,
        timeout=timeout,
        allowed_tools=tools,
    ):
        if event["type"] == "sentence":
            sentence_count += 1
            if on_sentence:
                await on_sentence(event["text"], sentence_count)
        elif event["type"] == "done":
            full_text = event["full_text"]

    if not full_text:
        return None

    # Parse tool call and emotion from the full text (same as non-streaming)
    tool_name, tool_arg, remainder = parse_tool_call(full_text)
    if tool_name:
        logger.info(f"[claude_cli:stream] Tool call: {tool_name}({tool_arg})")

    emotion, clean_reply = parse_emotion(remainder)
    clean_reply = _strip_thinking_preamble(clean_reply)
    clean_reply = _clean_reply_text(clean_reply, skip_truncate=True)

    logger.info(f"[claude_cli:stream] Parsed: [{emotion}] {clean_reply[:80]}... ({sentence_count} sentences streamed)")

    return {
        "reply": clean_reply,
        "emotion": emotion,
        "audio_url": None,
        "tool_name": tool_name,
        "tool_arg": tool_arg,
    }


async def chat_streaming(
    message: str,
    context: str = "telegram",
    user_name: str = "User",
    on_sentence: callable = None,
    reply_to: str | None = None,
    is_system_prompt: bool = False,
) -> dict:
    """Streaming variant of chat() for fast mode.

    Calls on_sentence(text, index) for each complete sentence as it streams
    from Claude. Falls back to regular chat() if not in Claude fast mode.

    Returns the same result dict as chat().
    """
    # Only use streaming for Claude fast mode
    if config.LLM_PROVIDER == "claude":
        has_tool_match = bool(tool_router.get_relevant_tools(message))
        mode = _detect_mode(message, has_tool_match)

        if mode == "fast":
            if config.CLAUDE_USE_SDK:
                # SDK path is non-streaming at the LLM level (the agentic loop owns
                # tool calls). Run it, then emit the completed reply's sentences so
                # TTS still chunks. Trades early-first-sentence latency for native tools.
                from services import claude_agent
                result = await claude_agent.run_agent(
                    message, context, user_name,
                    reply_to=reply_to, is_system_prompt=is_system_prompt,
                    model=config.get_llm_model(),
                )
                if result:
                    if on_sentence and result.get("reply"):
                        sentences = [s for s in re.split(r'(?<=[.!?。！？])\s', result["reply"]) if s.strip()]
                        for i, s in enumerate(sentences, 1):
                            await on_sentence(s.strip(), i)
                    return result
                logger.warning("[llm] SDK streaming path failed, falling back to regular chat")
            else:
                messages = _build_messages(message, context, user_name, False, reply_to=reply_to, is_system_prompt=is_system_prompt)
                result = await _stream_chat_with_claude_cli(
                    messages, config.get_llm_model(),
                    config.CLAUDE_CLI_EFFORT, config.CLAUDE_CLI_TIMEOUT, "",
                    on_sentence=on_sentence,
                )
                if result:
                    return result
                logger.warning("[llm] Claude CLI streaming failed, falling back to regular chat")

    # Fall back to regular (non-streaming) chat for non-fast modes or failures
    return await chat(message, context, user_name, reply_to=reply_to, is_system_prompt=is_system_prompt)


def _get_smart_system_prompt() -> str:
    return f"""You are {config.CHARACTER_NAME}, an AI companion. Stay in character but focus on giving useful, accurate answers. Use web search when needed.

Rules:
- Start EVERY reply with ONE emotion tag: [HAPPY], [SAD], [SURPRISED], [ANGRY], [THINKING], or [NEUTRAL]
- Be concise but thorough — no filler
- Stay in character but prioritize the answer
- Speak in English only"""


def _get_code_system_prompt() -> str:
    sandbox = config.CLAUDE_CLI_SANDBOX_PATH
    deploy_url = config.SANDBOX_DEPLOY_URL
    return f"""You are {config.CHARACTER_NAME}, an AI companion helping with coding tasks. Confident and competent.

Environment:
- You are running inside a Docker container on the host server
- Sandbox: {sandbox} — ALL files go here
- ALWAYS write files using absolute paths under {sandbox}/

Deployment:
- After building a web project, deploy it by running: python3 {sandbox}/deploy.py {sandbox}/<app-name>
- The deploy script auto-generates Dockerfile + docker-compose.yml for static sites (nginx)
- If you need a custom Dockerfile (e.g. Node.js app), write it yourself before deploying
- Deployed apps are reachable at: {deploy_url}
- ALWAYS deploy after building — don't just tell the user the command, run it yourself

Rules:
- Start EVERY reply with ONE emotion tag: [HAPPY], [SAD], [SURPRISED], [ANGRY], [THINKING], or [NEUTRAL]
- Focus on the code — build, fix, or explain as requested
- Keep non-code commentary to 1-2 sentences max
- Speak in English only"""

_FUNCTION_CALLING_ADDENDUM = """
IMPORTANT RULES FOR TOOL USE:
- ONLY call tools from the provided list. NEVER invent or hallucinate tool names.
- Only call a tool when the user EXPLICITLY wants an action performed.
- Do NOT call ANY tools for: casual chat, greetings, compliments, jokes, teasing, "I love you", "thank you", emotional messages, or general conversation. These are NEVER tool calls. Just reply naturally.
- When calling a tool, ALSO provide a text reply with an emotion tag — this text gets spoken aloud.

CRITICAL — EMOTION TAGS:
- You MUST start EVERY reply with an emotion tag: [HAPPY], [SAD], [SURPRISED], [ANGRY], [THINKING], or [NEUTRAL].
- Match the emotion to the conversation context. Do NOT default to [NEUTRAL] for everything.
- Happy news → [HAPPY]. Sad news → [SAD]. Unexpected → [SURPRISED]. Annoyed → [ANGRY]. Pondering → [THINKING].
- [NEUTRAL] is ONLY for truly mundane exchanges.

CRITICAL — TOOL CALLING BEHAVIOR:
- When the user's message clearly implies an action (spending money, adding tasks, checking weather/balance/calendar, changing costume, saving a memory), you MUST call the appropriate tool immediately. Do NOT ask clarifying questions instead.
- Examples: "i spent 20000 on food" → call add_expense. "remind me to buy groceries" → call add_task. "done with laundry" → call complete_task. "i already finished cooking" → call complete_task. "found money 10000" → call add_income. "note this: I like blue" → call save_memory.
- When you call a tool, you MUST also include a short text reply with an emotion tag. Never return an empty message."""


async def _chat_with_function_calling(client, messages: list, system_prompt: str,
                                       allowed_tools: set[str] | None = None) -> dict:
    """Chat using native OpenAI function calling (for cloud providers like Groq).

    If allowed_tools is provided, only those tool schemas are passed to the model.
    Pass an empty set to disable tool calling entirely.
    """
    openai_tools = tool_registry.get_openai_tools()
    if allowed_tools is not None:
        openai_tools = [
            t for t in openai_tools
            if t.get("function", {}).get("name") in allowed_tools
        ]

    # Append function-calling instructions to system prompt (only when tools are available)
    if openai_tools and messages and messages[0]["role"] == "system":
        messages[0]["content"] += _FUNCTION_CALLING_ADDENDUM

    kwargs = dict(
        model=config.get_llm_model(),
        messages=messages,
        temperature=0.7,
        max_tokens=200,
    )
    if openai_tools:
        kwargs["tools"] = openai_tools
        kwargs["tool_choice"] = "auto"

    response = await asyncio.wait_for(
        asyncio.to_thread(client.chat.completions.create, **kwargs),
        timeout=config.LLM_TIMEOUT + 5,
    )

    choice = response.choices[0]
    raw_reply = choice.message.content or ""
    tool_name = None
    tool_arg = None

    # Check for native function calls
    if choice.message.tool_calls:
        tc = choice.message.tool_calls[0]  # take first tool call
        tool_name = tc.function.name
        try:
            args = json.loads(tc.function.arguments)
            tool_arg = args.get("arg", "")
        except (json.JSONDecodeError, AttributeError):
            tool_arg = tc.function.arguments or ""
        # Normalize null/None to empty string
        if tool_arg is None:
            tool_arg = ""
        logger.info(f"[llm] Native function call: {tool_name}({tool_arg})")

    logger.info(f"[llm] Raw reply: {raw_reply[:200]}")

    # Fallback: parse <function=name{args}</function> if model embedded it in text
    if not tool_name and "<function=" in raw_reply:
        logger.info(f"[llm] Detected <function= in text, attempting parse...")
        tool_name, tool_arg, raw_reply = _parse_text_function_call(raw_reply)
        if tool_name:
            logger.info(f"[llm] Text-embedded function call: {tool_name}({tool_arg})")
        else:
            logger.warning(f"[llm] <function= detected but parse failed")

    # Parse emotion from text (still use emotion tags in text)
    emotion, clean_reply = parse_emotion(raw_reply)
    clean_reply = _clean_reply_text(clean_reply)

    # If no text reply but tool was called, don't store empty string
    # (Mistral sometimes returns tool_calls with no content)
    if not clean_reply and tool_name:
        clean_reply = ""

    logger.info(f"LLM response (function-calling): [{emotion}] {clean_reply[:80]}...")

    return {
        "reply": clean_reply,
        "emotion": emotion,
        "audio_url": None,
        "tool_name": tool_name,
        "tool_arg": tool_arg,
    }


async def _chat_with_text_tags(client, messages: list) -> dict:
    """Chat using text-tag parsing (for local providers like LM Studio)."""
    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.chat.completions.create,
            model=config.get_llm_model(),
            messages=messages,
            temperature=0.7,
            max_tokens=200,
        ),
        timeout=config.LLM_TIMEOUT + 5,
    )

    raw_reply = response.choices[0].message.content or ""
    logger.info(f"[llm] Raw reply: {raw_reply[:200]}")

    tool_name, tool_arg, remainder = parse_tool_call(raw_reply)
    if tool_name:
        logger.info(f"LLM tool call (text-tag): {tool_name}({tool_arg})")

    emotion, clean_reply = parse_emotion(remainder)
    clean_reply = _clean_reply_text(clean_reply)

    logger.info(f"LLM response (text-tag): [{emotion}] {clean_reply[:80]}...")

    return {
        "reply": clean_reply,
        "emotion": emotion,
        "audio_url": None,
        "tool_name": tool_name,
        "tool_arg": tool_arg,
    }


def _build_time_context() -> str:
    """Build the '## Current Context' block: time-of-day + elapsed time since the
    user's last message.

    Gives the character real temporal awareness in EVERY context, not a hardcoded
    activity list. The elapsed-time figure lets her judge whether anything the user
    claims to have done in that window is plausible (you can't finish a workout, a
    meal, an errand, or a prayer in 16 seconds), and acknowledge long pauses
    naturally. She decides what's reasonable — we just give her the real clock.
    """
    from utils import time as ltime
    _now = ltime.now()
    _time_str = ltime.fmt_time(_now, "%A %H:%M")
    _gap_note = ""
    try:
        _last_ts = memory.get_last_user_message_timestamp()
        if _last_ts:
            _gap_seconds = int(_now.timestamp() - _last_ts)
            if _gap_seconds < 0:
                _gap_seconds = 0
            if _gap_seconds < 60:
                _disp = f"{_gap_seconds} seconds"
            elif _gap_seconds < 3600:
                _disp = f"{_gap_seconds // 60} min"
            else:
                _disp = f"{_gap_seconds / 3600:.1f} hours"
            _gap_note = (
                f" About {_disp} have passed since the user's previous message. Use this real"
                " elapsed time when reasoning: if they claim to have just done something that"
                " would realistically take longer than that — any time-consuming activity —"
                " it's not plausible, so call out the timing and make them prove it instead of"
                " believing it. If a long while has passed, acknowledge the gap naturally."
            )
    except Exception:
        pass
    return f"\n\n## Current Context\nIt's {_time_str}.{_gap_note}"


def _build_messages(message: str, context: str, user_name: str, use_function_calling: bool, reply_to: str | None = None, is_system_prompt: bool = False) -> list:
    """Build the messages array for an LLM request."""
    system_prompt = get_system_prompt()

    # Always-on holistic awareness of her own features (auto-generated from
    # plugin manifests + main features). Distinct from the reactive per-message
    # tool docs injected just below.
    from services import capabilities
    system_prompt += capabilities.build_capability_summary()

    if not use_function_calling:
        tools_block = tool_router.build_tools_prompt(message)
        matched_tools = tool_router.get_relevant_tools(message)
        if tools_block:
            system_prompt += "\n\n" + tools_block
            logger.info(f"[llm] Tools injected for message: {matched_tools}")

    core_memories_block = memory.format_core_memories_for_prompt()
    if core_memories_block:
        system_prompt += core_memories_block

    summaries_block = memory.format_summaries_for_prompt()
    if summaries_block:
        system_prompt += summaries_block

    mood_modifier = mood.get_prompt_modifier()
    if mood_modifier:
        system_prompt += mood_modifier

    # avatar-phone context — so the character knows what's on their own phone
    # (contacts, last messages, today's PIN, top reminders, latest diary mood).
    phone_ctx = get_phone_context()
    if phone_ctx:
        system_prompt += "\n\n" + phone_ctx

    # User state — physical/mental snapshot derived from recent chat, refreshed
    # every ~10min by services/user_state.py. Colors tone without changing logic.
    try:
        from services import user_state
        state_block = user_state.format_for_prompt()
        if state_block:
            system_prompt += "\n\n" + state_block
    except Exception:
        pass

    # Live plugin context — up-to-the-moment state from plugins that define
    # build_chat_context() (e.g. the game's most recent PvP result). Keeps her
    # grounded in events that happen outside her chat tools (Mini App battles).
    try:
        from services import plugin_loader as _pl
        for block in _pl.get_chat_context_blocks():
            system_prompt += "\n\n" + block
    except Exception:
        pass

    system_prompt += _build_time_context()

    # Bilingual mode: when JP toggle is on, instruct LLM to respond in both languages
    if config.TTS_LANGUAGE == "jp":
        system_prompt += ("\n\n## BILINGUAL MODE (ACTIVE)\n"
            "Respond in BOTH English and Japanese. Use this exact format:\n"
            "en: [your English response]\n"
            "jp: [same response in casual Japanese (タメ口)]\n\n"
            "Rules for Japanese:\n"
            "- Match the same personality, slang, and tone as the English\n"
            "- Use casual Japanese: じゃん、だよ、でしょ、ってか — never keigo\n"
            "- Keep the roast energy: 草、マジで、バカ are fine\n"
            "- One emotion tag before both lines, not per line")

    context_note = f"\n\nCurrent context: message from {user_name} via {context}."

    # Background/system prompts: no chat history, just system + task as user message
    # Framed as inner thought to avoid prompt-injection detection
    if is_system_prompt:
        system_prompt += context_note
        messages = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": f"[Inner thought: {message}]"})
        return messages

    messages = [{"role": "system", "content": system_prompt + context_note}]

    history = memory.get_recent_messages(
        hours=config.MEMORY_CONTEXT_HOURS,
        soft_cap=config.MEMORY_CONTEXT_SOFT_CAP,
    )
    # Filter out empty assistant messages (Mistral rejects them)
    for msg in history:
        if msg["role"] == "assistant" and not msg.get("content", "").strip():
            msg["content"] = "..."
    messages.extend(history)
    if reply_to:
        messages.append({"role": "system", "content": f'[User is replying to this message: "{reply_to}"]'})
    messages.append({"role": "user", "content": message})
    return messages


def _build_smart_messages(message: str, context: str, user_name: str, reply_to: str | None = None) -> list:
    """Build compact messages for smart mode — personality + memories + history, no tools."""
    system_prompt = _get_smart_system_prompt()

    core_memories_block = memory.format_core_memories_for_prompt()
    if core_memories_block:
        system_prompt += core_memories_block

    mood_modifier = mood.get_prompt_modifier()
    if mood_modifier:
        system_prompt += mood_modifier

    phone_ctx = get_phone_context()
    if phone_ctx:
        system_prompt += "\n\n" + phone_ctx

    try:
        from services import user_state
        state_block = user_state.format_for_prompt()
        if state_block:
            system_prompt += "\n\n" + state_block
    except Exception:
        pass

    # Live plugin context — up-to-the-moment state from plugins that define
    # build_chat_context() (e.g. the game's most recent PvP result). Keeps her
    # grounded in events that happen outside her chat tools (Mini App battles).
    try:
        from services import plugin_loader as _pl
        for block in _pl.get_chat_context_blocks():
            system_prompt += "\n\n" + block
    except Exception:
        pass

    system_prompt += _build_time_context()

    context_note = f"\n\nCurrent context: message from {user_name} via {context}."
    messages = [{"role": "system", "content": system_prompt + context_note}]

    history = memory.get_recent_messages(
        hours=config.MEMORY_CONTEXT_HOURS,
        soft_cap=config.MEMORY_CONTEXT_SOFT_CAP,
    )
    for msg in history:
        if msg["role"] == "assistant" and not msg.get("content", "").strip():
            msg["content"] = "..."
    messages.extend(history)
    if reply_to:
        messages.append({"role": "system", "content": f'[User is replying to this message: "{reply_to}"]'})
    messages.append({"role": "user", "content": message})
    return messages


def _build_code_messages(message: str, context: str, user_name: str) -> list:
    """Build minimal messages for code mode — just prompt + user message, no history/memories."""
    context_note = f"\n\nCurrent context: message from {user_name} via {context}."
    messages = [{"role": "system", "content": _get_code_system_prompt() + context_note}]
    messages.append({"role": "user", "content": message})
    return messages


async def _try_provider(provider: str, base_url: str, api_key: str, model: str,
                        messages: list, use_function_calling: bool,
                        allowed_tools: set[str] | None = None) -> dict | None:
    """Try a single LLM provider. Returns result dict or None on failure."""
    if provider == "claude":
        return await _chat_with_claude_cli(
            [m.copy() for m in messages], model,
            effort=config.CLAUDE_CLI_EFFORT,
            timeout=config.CLAUDE_CLI_TIMEOUT, tools="",
        )
    try:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key or "no-key",
            timeout=config.LLM_TIMEOUT,
            max_retries=1,
        )
        # Override model for this call
        orig_model = config.LLM_MODEL
        config.LLM_MODEL = model

        if use_function_calling:
            result = await _chat_with_function_calling(
                client, [m.copy() for m in messages], "", allowed_tools=allowed_tools
            )
        else:
            result = await _chat_with_text_tags(client, [m.copy() for m in messages])

        config.LLM_MODEL = orig_model
        return result

    except (APIConnectionError, APITimeoutError, RateLimitError, asyncio.TimeoutError) as e:
        config.LLM_MODEL = orig_model
        logger.warning(f"LLM ({provider}) failed: {e}")
        return None
    except BadRequestError as e:
        config.LLM_MODEL = orig_model
        # Try to salvage from failed_generation (Groq quirk)
        error_body = getattr(e, "body", {}) or {}
        failed_gen = error_body.get("failed_generation", "") if isinstance(error_body, dict) else ""
        if failed_gen:
            logger.warning(f"[llm] {provider} tool_use_failed, parsing failed_generation")
            tool_name, tool_arg, remainder = _parse_text_function_call(failed_gen)
            if not tool_name:
                tool_name, tool_arg, remainder = parse_tool_call(failed_gen)
            emotion, clean_reply = parse_emotion(remainder)
            clean_reply = _clean_reply_text(clean_reply)
            return {
                "reply": clean_reply, "emotion": emotion, "audio_url": None,
                "tool_name": tool_name, "tool_arg": tool_arg,
            }
        logger.warning(f"LLM ({provider}) BadRequest: {e}")
        return None
    except Exception as e:
        config.LLM_MODEL = orig_model
        logger.error(f"LLM ({provider}) error: {e}")
        return None


def _resolve_allowed_tools(message: str, is_system_prompt: bool) -> set[str]:
    """Filter tool names by TOOL_CALL_MODE + relevance to current message.

    - is_system_prompt=True means idle/background — not restricted by semi_off.
    - Relevance filtering (only tools matching the message) prevents GPT-OSS
      from re-calling unrelated tools based on conversation history.
    """
    mode = config.TOOL_CALL_MODE

    if mode == "off":
        return set()
    if mode == "semi_off" and not is_system_prompt:
        return set()

    relevant = set(tool_router.get_relevant_tools(message))
    # Memory tools are always active (save/update/delete_memory).
    relevant |= {"save_memory", "update_memory", "delete_memory"}

    if mode == "semi_normal" and not is_system_prompt:
        relevant &= tool_registry.MAIN_TOOLS

    return relevant


async def chat(message: str, context: str = "telegram", user_name: str = "User", image_path: str | None = None, reply_to: str | None = None, is_system_prompt: bool = False) -> dict:
    """
    Send a message to the LLM with fallback chain.

    Tries: primary → fallback_1 → fallback_2 → offline reply.
    Cloud providers use native function calling; local use text-tag parsing.

    Returns: {"reply": str, "emotion": str, "audio_url": None,
              "tool_name": str|None, "tool_arg": str|None}
    """
    # Build effective message with image context if provided
    effective_message = message
    if image_path:
        image_note = f"[User sent an image: {image_path}]\nUse the Read tool to view it, then respond.\n\n"
        if message and message != "[User sent an image]":
            effective_message = image_note + f"User says: {message}"
        else:
            effective_message = image_note + "React to this image naturally — comment on what you see."

    # Claude CLI mode-aware routing
    if config.LLM_PROVIDER == "claude":
        has_tool_match = bool(tool_router.get_relevant_tools(message))
        mode = _detect_mode(message, has_tool_match)
        logger.info(f"[llm] Claude mode: {mode} (tool_match={has_tool_match}, image={'yes' if image_path else 'no'})")

        # Force fast mode for image messages (needs Read tool)
        if image_path and mode == "code":
            mode = "fast"

        if mode == "fast":
            if config.CLAUDE_USE_SDK and not image_path:
                # Native tool calling via Claude Agent SDK (in-process MCP server).
                # Model sees all tools, calls them itself in an agentic loop — no
                # text-tags, no keyword gate. None → falls through to fallback chain.
                from services import claude_agent
                result = await claude_agent.run_agent(
                    effective_message, context, user_name,
                    reply_to=reply_to, is_system_prompt=is_system_prompt,
                    model=config.get_llm_model(),
                )
            else:
                # Legacy Haiku + text-tag mode (existing avatar-server tools)
                messages = _build_messages(effective_message, context, user_name, False, reply_to=reply_to, is_system_prompt=is_system_prompt)
                tools = "Read" if image_path else ""
                result = await _chat_with_claude_cli(
                    messages, config.get_llm_model(),
                    config.CLAUDE_CLI_EFFORT, config.CLAUDE_CLI_TIMEOUT, tools,
                )
        elif mode == "smart":
            messages = _build_smart_messages(effective_message, context, user_name, reply_to=reply_to)
            tools = config.CLAUDE_CLI_SMART_TOOLS
            if image_path:
                tools = f"{tools},Read" if tools else "Read"
            result = await _chat_with_claude_cli(
                messages, config.CLAUDE_CLI_SMART_MODEL,
                config.CLAUDE_CLI_SMART_EFFORT, config.CLAUDE_CLI_SMART_TIMEOUT,
                tools,
            )
        elif mode == "code":
            messages = _build_code_messages(effective_message, context, user_name)
            result = await _chat_with_claude_cli(
                messages, config.CLAUDE_CLI_CODE_MODEL,
                config.CLAUDE_CLI_CODE_EFFORT, config.CLAUDE_CLI_CODE_TIMEOUT,
                config.CLAUDE_CLI_CODE_TOOLS, cwd=config.CLAUDE_CLI_SANDBOX_PATH,
            )
        else:
            result = None

        if result:
            return result
        logger.warning("[llm] Claude CLI failed, falling through to fallbacks")

    else:
        use_fc = _is_cloud_provider()
        # Fallback providers don't support images — use original message
        messages = _build_messages(message, context, user_name, use_fc)

        allowed_tools = _resolve_allowed_tools(message, is_system_prompt)
        if config.TOOL_CALL_MODE != "normal":
            logger.info(f"[llm] TOOL_CALL_MODE={config.TOOL_CALL_MODE}, allowed_tools={sorted(allowed_tools)}")

        # Try primary provider
        result = await _try_provider(
            provider=config.LLM_PROVIDER,
            base_url=config.get_llm_base_url(),
            api_key=config.get_llm_api_key(),
            model=config.get_llm_model(),
            messages=messages,
            use_function_calling=use_fc,
            allowed_tools=allowed_tools,
        )
        if result:
            return result

    # Try fallback chain — fallback providers don't support images, use original message
    use_fc = _is_cloud_provider()  # re-evaluate for fallback message building
    messages = _build_messages(message, context, user_name, use_fc) if config.LLM_PROVIDER == "claude" else messages
    allowed_tools = _resolve_allowed_tools(message, is_system_prompt)
    for fb in config.get_llm_fallback_chain():
        fb_is_cloud = fb["provider"] not in ("lmstudio", "ollama", "claude")
        if fb_is_cloud != use_fc:
            # Rebuild messages if switching between cloud/local mode
            messages = _build_messages(message, context, user_name, fb_is_cloud)
        logger.info(f"[llm] Trying fallback: {fb['provider']} ({fb['model']})")
        result = await _try_provider(
            provider=fb["provider"],
            base_url=fb["base_url"],
            api_key=fb["api_key"],
            model=fb["model"],
            messages=messages,
            use_function_calling=fb_is_cloud,
            allowed_tools=allowed_tools,
        )
        if result:
            logger.info(f"[llm] Fallback {fb['provider']} succeeded")
            return result

    # All providers failed
    logger.warning("[llm] All providers failed, returning offline reply")
    return {
        "reply": config.OFFLINE_REPLY,
        "emotion": config.DEFAULT_EMOTION,
        "audio_url": None,
        "tool_name": None,
        "tool_arg": None,
    }


async def check_llm() -> str:
    """Check if any LLM provider in the chain is reachable. Returns 'ok' or 'offline'."""
    # Check claude CLI
    if config.LLM_PROVIDER == "claude":
        if await check_claude_cli():
            return "ok"
    else:
        # Check primary (OpenAI-compatible)
        try:
            client = OpenAI(
                base_url=config.get_llm_base_url(),
                api_key=config.get_llm_api_key() or "no-key",
                timeout=5,
                max_retries=0,
            )
            await asyncio.to_thread(client.models.list)
            return "ok"
        except Exception:
            pass

    # Check fallbacks
    for fb in config.get_llm_fallback_chain():
        try:
            client = OpenAI(
                base_url=fb["base_url"],
                api_key=fb["api_key"] or "no-key",
                timeout=5,
                max_retries=0,
            )
            await asyncio.to_thread(client.models.list)
            return "ok"
        except Exception:
            continue

    return "offline"


# Backward compat alias
check_lm_studio = check_llm

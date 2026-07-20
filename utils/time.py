"""Local-time helper — single source of truth for the character's timezone.

Timezone comes from config (`TIMEZONE` IANA name, else a fixed `TZ_OFFSET_HOURS`
offset), which is populated from `.env` or `character.json` at startup. Everything
in the engine and plugins should call these helpers instead of hardcoding an
offset, so a user in any timezone only has to set one config value.

Resolution is lazy (inside the functions) so it reflects `config.load_character()`,
which runs during app startup — after this module is first imported.
"""

from datetime import datetime, timedelta, timezone, tzinfo

import config


def get_tz() -> tzinfo:
    """Return the configured timezone.

    Prefers the IANA name in config.TIMEZONE (via zoneinfo); falls back to a fixed
    UTC offset from config.TZ_OFFSET_HOURS. Never raises — an unknown/unsupported
    IANA name degrades to the offset.
    """
    name = (getattr(config, "TIMEZONE", "") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            # zoneinfo missing (no tzdata on Windows) or bad name → use offset
            pass
    return timezone(timedelta(hours=getattr(config, "TZ_OFFSET_HOURS", 0.0)))


def now() -> datetime:
    """Current timezone-aware datetime in the configured timezone."""
    return datetime.now(get_tz())


def tz_label() -> str:
    """Short display label for the timezone (e.g. 'WIB'), or '' if unset."""
    return (getattr(config, "TZ_LABEL", "") or "").strip()


def today_str(fmt: str = "%Y-%m-%d") -> str:
    """Local date as a string (default ISO date)."""
    return now().strftime(fmt)


def fmt_time(dt: datetime | None = None, fmt: str = "%H:%M") -> str:
    """Format a datetime (default: now) and append the tz label when present."""
    dt = dt or now()
    label = tz_label()
    base = dt.strftime(fmt)
    return f"{base} {label}".rstrip() if label else base

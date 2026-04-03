"""Character song catalogue — loaded from character.json or hardcoded fallback.

Songs are searched on Spotify by "character_name + song_title" to find the right version.
"""

import random

# Default catalogue — Hoshimachi Suisei's major songs
# These are searched as "{character_name} {title}" on Spotify
DEFAULT_SONGS = [
    "Stellar Stellar",
    "NEXT COLOR PLANET",
    "Bye Bye Rainy",
    "Ghost",
    "Tenkyuu Suisei wa Yoru wo Mataide",
    "TEMPLATE",
    "Bibbidiba",
    "Idol Keikaku",
    "Bluerose",
    "Michizure",
    "WINNER",
    "Soiree",
    "Hoshimachi Cooking",
    "Sakura no Ame",
    "Comet",
    "TRUE GIRL SHOW",
    "Jibunkatte Dazzling",
    "Suki Suki Daisuki",
]

# Common misspellings / aliases → canonical title
ALIASES = {
    "bibideba": "Bibbidiba",
    "bibbideba": "Bibbidiba",
    "bibidiba": "Bibbidiba",
    "ビビデバ": "Bibbidiba",
    "ncp": "NEXT COLOR PLANET",
    "next color": "NEXT COLOR PLANET",
    "tenkyuu": "Tenkyuu Suisei wa Yoru wo Mataide",
    "天球": "Tenkyuu Suisei wa Yoru wo Mataide",
    "jibunkatte": "Jibunkatte Dazzling",
    "自分勝手": "Jibunkatte Dazzling",
    "suki suki": "Suki Suki Daisuki",
    "idol": "Idol Keikaku",
    "bye bye": "Bye Bye Rainy",
}

_songs: list[str] = list(DEFAULT_SONGS)


def get_songs() -> list[str]:
    return list(_songs)


def get_random() -> str:
    return random.choice(_songs)


def find(query: str) -> str | None:
    """Fuzzy match a song title from the catalogue."""
    q = query.lower().strip()
    # Check aliases first
    for alias, canonical in ALIASES.items():
        if alias in q or q in alias:
            return canonical
    # Exact match
    for s in _songs:
        if s.lower() == q:
            return s
    # Partial match
    for s in _songs:
        if q in s.lower() or s.lower() in q:
            return s
    return None

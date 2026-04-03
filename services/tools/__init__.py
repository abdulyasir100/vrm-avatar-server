"""Auto-register MAIN FEATURE tool handlers on import.

Plugins (todo, money, calorie, weather, calendar, anime, meme) are loaded
by services/plugin_loader.py — not here.

Only Unity-dependent and core system tools stay here.
"""

from services.tools import costume_tool   # noqa: F401  # Unity WebSocket
from services.tools import memory_tool    # noqa: F401  # Core system
from services.tools import gacha_tool     # noqa: F401  # Unity WebSocket
from services.tools import roulette_tool  # noqa: F401  # Unity WebSocket
from services.tools import thr_tool       # noqa: F401  # Unity WebSocket
from services.tools import sensor_tool    # noqa: F401  # Core system

"""Wishlist plugin — shopping list with prices."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Wishlist storage not initialized")
    return _storage


def _parse_price(text: str) -> float | None:
    """Extract price from text. Supports: 50000, 50k, 50rb, $50."""
    text = text.strip().lower().replace(",", "").replace(".", "")
    m = re.match(r"^\$?(\d+(?:\.\d+)?)\s*(?:k|rb|ribu)?$", text)
    if not m:
        return None
    val = float(m.group(1))
    if "k" in text or "rb" in text or "ribu" in text:
        val *= 1000
    return val


def _format_price(price: float | None) -> str:
    if price is None:
        return ""
    if price >= 1000:
        return f" ({price:,.0f})"
    return f" ({price:,.2f})"


async def handle_add_wish(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Add item to wishlist. Arg: name|price or just name."""
    storage = _get_storage()
    parts = [p.strip() for p in arg.split("|")]
    name = parts[0]
    if not name:
        return {"ok": False, "result": "Need an item name.", "side_effects": []}

    price = _parse_price(parts[1]) if len(parts) > 1 else None

    existing = storage.find_by_name(name)
    if existing:
        return {"ok": False, "result": f"'{existing['name']}' is already on the list.", "side_effects": []}

    item = storage.add_item(name, price)
    price_str = _format_price(price) if price else ""
    return {"ok": True, "result": f"Added to wishlist: {item['name']}{price_str}", "side_effects": []}


async def handle_check_wishlist(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Show all wishlist items."""
    storage = _get_storage()
    items = storage.get_all()

    if not items:
        return {"ok": True, "result": "Wishlist is empty.", "side_effects": []}

    lines = []
    for i, item in enumerate(items, 1):
        price_str = _format_price(item["price"]) if item["price"] else ""
        lines.append(f"{i}. {item['name']}{price_str}")

    total = storage.get_total()
    if total > 0:
        lines.append(f"\nTotal: {total:,.0f}")

    return {"ok": True, "result": f"Wishlist ({len(items)} items):\n" + "\n".join(lines), "side_effects": []}


async def handle_callback(action: str, item_id: str) -> dict | None:
    storage = _get_storage()
    if action == "bought":
        item = storage.get_by_id(item_id)
        if not item:
            return {"message": "Item not found.", "refresh": False}
        storage.mark_bought(item_id)
        return {"message": f"'{item['name']}' bought!", "refresh": True}
    if action == "remove":
        item = storage.get_by_id(item_id)
        if not item:
            return {"message": "Item not found.", "refresh": False}
        storage.remove(item_id)
        return {"message": f"'{item['name']}' removed.", "refresh": True}
    return None


async def cmd_list(args: str = "") -> dict:
    storage = _get_storage()
    items = storage.get_all()

    if not items:
        return {"text": "Wishlist is empty. Use /p.wishlist.add <name>|<price>", "inline_keyboard": None}

    lines = []
    keyboard = []
    for item in items:
        price_str = f" — {item['price']:,.0f}" if item["price"] else ""
        lines.append(f"- {item['name']}{price_str}")
        keyboard.append([
            {"text": f"Bought: {item['name']}", "callback_data": f"plugin:wishlist:bought:{item['id']}"},
            {"text": "X", "callback_data": f"plugin:wishlist:remove:{item['id']}"},
        ])

    total = storage.get_total()
    header = f"Wishlist ({len(items)} items)"
    if total > 0:
        header += f" — Total: {total:,.0f}"

    return {"text": header + "\n\n" + "\n".join(lines), "inline_keyboard": keyboard}


async def cmd_add(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.wishlist.add <name>|<price>\nExample: /p.wishlist.add PS5|7500000", "inline_keyboard": None}
    result = await handle_add_wish(args, {})
    return {"text": result["result"], "inline_keyboard": None}


async def cmd_bought(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.wishlist.bought <name>", "inline_keyboard": None}
    storage = _get_storage()
    item = storage.get_by_id(args.strip()) or storage.find_by_name(args.strip())
    if not item:
        return {"text": f"No item matching '{args}'.", "inline_keyboard": None}
    storage.mark_bought(item["id"])
    return {"text": f"'{item['name']}' marked as bought!", "inline_keyboard": None}


async def cmd_remove(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.wishlist.remove <name>", "inline_keyboard": None}
    storage = _get_storage()
    item = storage.get_by_id(args.strip()) or storage.find_by_name(args.strip())
    if not item:
        return {"text": f"No item matching '{args}'.", "inline_keyboard": None}
    storage.remove(item["id"])
    return {"text": f"'{item['name']}' removed.", "inline_keyboard": None}

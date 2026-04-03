"""Money plugin — tool handlers for expense/income tracking."""

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)
_storage = None

# Spending nag state (per-session dedup)
_last_spending_nag: str = ""

_SPENDING_PROMPTS = {
    "warning": [
        "The user has spent {percent}% of their monthly budget. Scold them lightly — they're halfway through. Be in-character. One sentence.",
        "The user already used {percent}% of their budget this month. Comment with concern and sass. One sentence.",
    ],
    "critical": [
        "The user has spent {percent}% of their monthly budget! That's way too much. Be genuinely annoyed. One sentence.",
        "The user blew through {percent}% of their budget. Express shock and disappointment. One sentence.",
    ],
}


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Money plugin storage not initialized")
    return _storage


def _format_rp(amount: int) -> str:
    """Format amount as Indonesian Rupiah: Rp 50.000"""
    return f"Rp {amount:,}".replace(",", ".")


def _speak_rp(amount: int) -> str:
    """TTS-friendly amount: 50 thousand rupiah, 1.5 million rupiah"""
    if amount >= 1_000_000:
        m = amount / 1_000_000
        return f"{m:.1f} million rupiah".replace(".0 ", " ")
    elif amount >= 1_000:
        k = amount / 1_000
        return f"{k:.0f} thousand rupiah"
    return f"{amount} rupiah"


async def handle_add_expense(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Record expenses. Arg: 'amount|desc' or 'amount|desc,amount|desc' for multiple."""
    storage = _get_storage()
    items = [i.strip() for i in arg.split(",") if i.strip()]
    if not items:
        return {"ok": False, "result": "No expense data provided.", "side_effects": []}

    total = 0
    created = []
    for item in items:
        parts = item.split("|")
        if len(parts) < 2:
            continue
        try:
            amount = int(parts[0].strip().replace(".", "").replace(",", ""))
        except ValueError:
            continue
        desc = parts[1].strip()
        category = parts[2].strip() if len(parts) > 2 else ""
        storage.create_transaction("expense", amount, desc, category)
        total += amount
        created.append(f"{desc} ({_format_rp(amount)})")

    if not created:
        return {"ok": False, "result": "Could not parse expense data.", "side_effects": []}

    summary = ", ".join(created)
    return {
        "ok": True,
        "result": f"Recorded expense: {summary}. Total: {_speak_rp(total)}.",
        "side_effects": [],
    }


async def handle_add_income(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Record income. Arg: 'amount|desc' or 'amount|desc|category'."""
    storage = _get_storage()
    parts = arg.split("|")
    if len(parts) < 2:
        return {"ok": False, "result": "Format: amount|description", "side_effects": []}

    try:
        amount = int(parts[0].strip().replace(".", "").replace(",", ""))
    except ValueError:
        return {"ok": False, "result": "Invalid amount.", "side_effects": []}

    desc = parts[1].strip()
    category = parts[2].strip() if len(parts) > 2 else ""
    storage.create_transaction("income", amount, desc, category)

    return {
        "ok": True,
        "result": f"Recorded income: {desc} ({_speak_rp(amount)}).",
        "side_effects": [],
    }


async def handle_check_balance(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check balance and spending status."""
    storage = _get_storage()
    balance = storage.get_balance()
    status = storage.get_spending_status()

    lines = [
        f"Balance: {_speak_rp(balance['balance'])}",
        f"This month: earned {_speak_rp(balance['monthly_income'])}, spent {_speak_rp(balance['monthly_expense'])}",
    ]
    if status["monthly_budget"] > 0:
        lines.append(f"Budget usage: {status['spent_percent']:.0f}% of {_speak_rp(status['monthly_budget'])}")

    return {"ok": True, "result": " | ".join(lines), "side_effects": []}


async def handle_callback(action: str, item_id: str) -> dict | None:
    """Handle Telegram inline button callbacks."""
    storage = _get_storage()
    if action == "delete":
        if storage.delete_transaction(item_id):
            return {"message": "Transaction deleted.", "refresh": True}
    return None


async def check_spending():
    """Background check: return spending nag reminders if thresholds crossed."""
    global _last_spending_nag
    storage = _get_storage()
    status = storage.get_spending_status()
    threshold = status.get("threshold", "ok")
    percent = status.get("spent_percent", 0)

    if threshold == "ok" or threshold == _last_spending_nag:
        return []

    prompts = _SPENDING_PROMPTS.get(threshold, [])
    if not prompts:
        return []

    _last_spending_nag = threshold
    prompt = random.choice(prompts).format(percent=percent)

    return [{
        "type": "spending_nag",
        "prompt": prompt,
        "context": f"spending_{threshold}",
        "notify": {
            "title": f"Spending {threshold.upper()}",
            "message": f"You've spent {percent}% of your monthly budget!",
            "priority": 4,
            "tags": ["money_with_wings"],
        },
    }]


# --- Telegram command handlers (for /p.balance, /p.expenses, /p.balance.budget) ---

async def cmd_balance(args: str = "") -> dict:
    """Show balance summary."""
    storage = _get_storage()
    balance = storage.get_balance()
    status = storage.get_spending_status()

    lines = [
        f"💰 Balance: {_format_rp(balance['balance'])}",
        f"📈 Income (month): {_format_rp(balance['monthly_income'])}",
        f"📉 Expense (month): {_format_rp(balance['monthly_expense'])}",
    ]
    if status["monthly_budget"] > 0:
        pct = status["spent_percent"]
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        lines.append(f"📊 Budget: [{bar}] {pct:.0f}%")
        lines.append(f"   {_format_rp(balance['monthly_expense'])} / {_format_rp(status['monthly_budget'])}")

    return {"text": "\n".join(lines), "inline_keyboard": None}


async def cmd_expenses(args: str = "") -> dict:
    """Show recent expenses with delete buttons."""
    storage = _get_storage()
    txns = storage.get_transactions(tx_type="expense", limit=8)
    if not txns:
        return {"text": "📉 No expenses recorded.", "inline_keyboard": None}

    lines = []
    rows = []
    for i, tx in enumerate(txns, 1):
        desc = tx.get("description", "")[:20] or "unnamed"
        lines.append(f"{i}. {_format_rp(tx['amount'])} — {desc}")
        rows.append([
            {"text": f"🗑 {desc[:15]}", "callback_data": f"plugin:money:delete:{tx['id']}"},
        ])

    text = f"📉 Recent Expenses ({len(txns)}):\n" + "\n".join(lines)
    return {"text": text, "inline_keyboard": rows}


async def cmd_set_budget(args: str = "") -> dict:
    """Set monthly budget."""
    storage = _get_storage()
    if args:
        try:
            amount = int(args.strip().replace(".", "").replace(",", ""))
            storage.set_budget(amount)
            return {"text": f"✅ Monthly budget set to {_format_rp(amount)}", "inline_keyboard": None}
        except ValueError:
            return {"text": "❌ Invalid amount. Use: /p.balance.budget 5000000", "inline_keyboard": None}

    current = storage.get_spending_status()
    budget = current["monthly_budget"]
    if budget > 0:
        return {"text": f"Current budget: {_format_rp(budget)}\nReply with new amount to change.", "inline_keyboard": None}
    return {"text": "No budget set. Reply with amount: /p.balance.budget 5000000", "inline_keyboard": None}

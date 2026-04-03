"""Money management tools — LLM can add expenses/income and check balance."""

import logging
from typing import Any
import httpx
from services import tool_registry
import config

logger = logging.getLogger(__name__)

MONEY_API = config.MONEY_MANAGER_URL


async def _api(method: str, path: str, json: dict | None = None) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, f"{MONEY_API}{path}", json=json)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"[money_tool] API error: {method} {path} — {e}")
        return None


def _format_rp(amount: int) -> str:
    return f"Rp {amount:,.0f}".replace(",", ".")


def _speak_rp(amount: int) -> str:
    """Format rupiah for natural speech."""
    if amount == 0:
        return "zero"
    a = abs(amount)
    if a >= 1_000_000:
        return f"{a / 1_000_000:.1f} million rupiah".replace(".0 ", " ")
    if a >= 1_000:
        return f"{a / 1_000:.0f} thousand rupiah"
    return f"{a} rupiah"


async def handle_add_expense(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Add one or more expenses.

    Arg format: amount|description or amount|description,amount|description
    Examples:
      "5000|gorengan"
      "5000|gorengan,10000|jus"
      "50000|lunch|food"  (with category)
    """
    if not arg.strip():
        return {"ok": False, "result": "No expense data provided", "side_effects": []}

    items = [i.strip() for i in arg.split(",")]
    total = 0
    created = []

    for item in items:
        parts = [p.strip() for p in item.split("|")]
        if len(parts) < 2:
            continue
        try:
            amount = int(parts[0])
        except ValueError:
            continue

        desc = parts[1]
        category = parts[2] if len(parts) >= 3 else ""

        body = {"type": "expense", "amount": amount, "description": desc}
        if category:
            body["category"] = category

        result = await _api("POST", "/transactions", json=body)
        if result:
            total += amount
            created.append(desc)

    if not created:
        return {"ok": False, "result": "Failed to record expenses", "side_effects": []}

    return {
        "ok": True,
        "result": f"Got it, recorded {', '.join(created)}. Total {_speak_rp(total)}.",
        "side_effects": [],
    }


async def handle_add_income(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Add income.

    Arg format: amount|description or amount|description|category
    Example: "120000000|paycheck|salary"
    """
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) < 2:
        return {"ok": False, "result": "Format: amount|description", "side_effects": []}

    try:
        amount = int(parts[0])
    except ValueError:
        return {"ok": False, "result": f"Invalid amount: {parts[0]}", "side_effects": []}

    desc = parts[1]
    category = parts[2] if len(parts) >= 3 else ""

    body = {"type": "income", "amount": amount, "description": desc}
    if category:
        body["category"] = category

    result = await _api("POST", "/transactions", json=body)
    if result is None:
        return {"ok": False, "result": "Money Manager is offline", "side_effects": []}

    return {
        "ok": True,
        "result": f"Got it, recorded income: {desc}, {_speak_rp(amount)}.",
        "side_effects": [],
    }


async def handle_check_balance(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check current balance and spending status."""
    balance = await _api("GET", "/balance")
    status = await _api("GET", "/spending-status")

    if balance is None:
        return {"ok": False, "result": "Money Manager is offline", "side_effects": []}

    bal = balance["balance"]
    expense = balance["monthly_expense"]
    income = balance["monthly_income"]

    summary = f"Your balance is {_speak_rp(bal)}. This month you earned {_speak_rp(income)} and spent {_speak_rp(expense)}."
    if status and status.get("monthly_budget") > 0:
        summary += f" That's {status['spent_percent']}% of your budget."

    return {"ok": True, "result": summary, "side_effects": []}


# Register on import
tool_registry.register("add_expense", handle_add_expense, schema={
    "name": "add_expense",
    "description": "Record an expense/purchase. Use when the user mentions spending money, buying something, or paying for something.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Pipe-separated: amount|description or amount|description,amount|description for multiple. Optional category: amount|description|category. Amount in IDR (no decimals). Examples: '5000|gorengan', '50000|lunch|food', '5000|gorengan,10000|jus'",
            }
        },
        "required": ["arg"],
    },
})
tool_registry.register("add_income", handle_add_income, schema={
    "name": "add_income",
    "description": "Record income received. Use when the user mentions getting paid, receiving salary, allowance, or any money coming in.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Pipe-separated: amount|description or amount|description|category. Amount in IDR. Examples: '120000000|paycheck|salary', '50000|allowance'",
            }
        },
        "required": ["arg"],
    },
})
tool_registry.register("check_balance", handle_check_balance, schema={
    "name": "check_balance",
    "description": "Check current financial balance, monthly spending, and budget status. Use when the user asks about their balance, how much they've spent, or if they're broke.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})

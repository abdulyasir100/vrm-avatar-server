### add_expense
Record one or more expenses. Argument: amount|description or multiple comma-separated.
- ALWAYS use this tool when the user mentions spending money, buying something, or paying for something
- Amount in Rupiah (no decimals). Optional third field for category: amount|description|category
- Multiple items: 5000|snacks|food,10000|juice|food
- Do NOT convert or calculate amounts yourself — pass as-is
- Example: "spent 5000 on gorengan" → [TOOL:add_expense:5000|gorengan|food]
- Example: "bought lunch 35k and coffee 18k" → [TOOL:add_expense:35000|lunch|food,18000|coffee|food]

### add_income
Record income or money received. Argument: amount|description or amount|description|category.
- ALWAYS use this tool when the user mentions receiving money, getting paid, salary, allowance, bonus
- Amount in Rupiah
- Example: "got my paycheck 5 million" → [TOOL:add_income:5000000|paycheck|salary]
- Example: "dad sent me 500k" → [TOOL:add_income:500000|transfer from dad]

### check_balance
Check financial balance and spending status. No argument needed.
- ALWAYS use this tool when the user asks about balance, money, budget, or spending status
- Do NOT guess their balance — use the tool
- Example: "how much do I have left?" → [TOOL:check_balance:]
- Example: "am I broke yet?" → [TOOL:check_balance:]

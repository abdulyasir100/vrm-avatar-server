### add_meal
Log a meal with automatic calorie lookup. Argument: meal_type|food_name or meal_type|food_name|portion.
- ALWAYS use this tool when the user mentions eating, drinking, having a meal, or logging food
- meal_type: breakfast, lunch, dinner, snack (or Indonesian: sarapan, siang, malam, cemilan)
- Split each food item separately. Multiple items: snack|gorengan,snack|es teh
- Do NOT estimate calories yourself — the system looks them up automatically
- Example: "ate nasi goreng for lunch" → [TOOL:add_meal:lunch|nasi goreng]
- Example: "had 2 slices of pizza and a coke" → [TOOL:add_meal:snack|pizza,snack|coke]
- Example: "sarapan indomie" → [TOOL:add_meal:breakfast|indomie]

### check_calories
Check today's calorie intake and remaining budget. No argument needed.
- ALWAYS use this tool when the user asks about calories, how much they've eaten, or their intake
- Do NOT guess their calorie count — use the tool
- Example: "how many calories today?" → [TOOL:check_calories:]
- Example: "am I eating too much?" → [TOOL:check_calories:]

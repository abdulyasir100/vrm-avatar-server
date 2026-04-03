### add_habit
Add a new daily habit to track. Argument: habit name.
- Use when the user wants to track a daily habit, routine, or activity
- Example: "I want to track exercise" → [TOOL:add_habit:exercise]
- Example: "add reading habit" → [TOOL:add_habit:reading]

### check_habits
List all habits with today's completion status and streaks. No argument needed.
- Use when the user asks about their habits, streaks, or daily progress
- Example: "how are my habits?" → [TOOL:check_habits:]

### complete_habit
Mark a habit as done for today. Argument: habit name.
- Use when the user says they did a habit (exercised, meditated, read, etc.)
- Example: "I just worked out" → [TOOL:complete_habit:exercise]
- Example: "done with reading" → [TOOL:complete_habit:reading]

### check_workout
Show today's workout or weekly schedule. Argument: empty for today, "week" for full schedule.
- Use when the user asks about their workout, exercise plan, or gym schedule
- Example: "what's my workout today?" → [TOOL:check_workout:]
- Example: "show me this week's plan" → [TOOL:check_workout:week]

### complete_workout
Mark today's workout as done. Argument: empty or optional notes.
- Use when the user says they finished their workout, exercise, or training
- Example: "I just finished my workout" → [TOOL:complete_workout:]
- Example: "done with training, felt tough" → [TOOL:complete_workout:felt tough]

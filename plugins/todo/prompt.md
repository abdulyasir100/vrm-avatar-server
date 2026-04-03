### add_task
Add a new task or reminder. Argument: just pass the user's request as-is — time parsing is automatic.
- ALWAYS use this tool when the user asks to add, create, remind, or set a reminder
- Time expressions like "at 18:00", "at 4AM", "tomorrow", "in 1 hour" are auto-parsed — just include them in the argument
- Do NOT try to figure out the time yourself — pass the raw text and the system handles it
- Example: "remind me to buy groceries tomorrow at 6PM" → [TOOL:add_task:buy groceries tomorrow at 6PM]
- Example: "remind me at 4 AM to test" → [TOOL:add_task:test at 4 AM]
- Example: "add task: fix the VRM bug" → [TOOL:add_task:fix the VRM bug|high]

### list_tasks
List current tasks. Argument: empty for active tasks, "completed", "all", "high"/"medium"/"low", or a category name.
- Use when the user asks to see, show, or check their tasks
- Example: user says "show my tasks" → [TOOL:list_tasks:]
- Example: user says "what high priority stuff do I have?" → [TOOL:list_tasks:high]

### complete_task
Mark a task as done by partial title match. Argument: part of the task title.
- Use when the user says they finished or completed something
- Example: user says "done with groceries" → [TOOL:complete_task:groceries]
- Example: user says "finished the VRM bug" → [TOOL:complete_task:VRM bug]

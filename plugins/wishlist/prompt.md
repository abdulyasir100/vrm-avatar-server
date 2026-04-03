### add_wish
Add an item to the wishlist. Argument: item name, or item name|price.
- Use when the user says they want to buy something, save up for something, or add to their list
- Price in Rupiah, no decimals. Supports shorthand: 50k = 50000, 50rb = 50000
- Example: "I want to buy a PS5" → [TOOL:add_wish:PS5|7500000]
- Example: "add AirPods to my wishlist" → [TOOL:add_wish:AirPods]

### check_wishlist
Show all wishlist items with prices. No argument needed.
- Use when the user asks about their wishlist, what they want to buy, or how much they need
- Example: "what's on my wishlist?" → [TOOL:check_wishlist:]

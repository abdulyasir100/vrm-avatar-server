---
title: Suisei TTS
emoji: ☄️
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: 5.33.0
app_file: app.py
pinned: false
---

Qwen3-TTS voice clone for Hoshimachi Suisei. Used by avatar-server as cloud TTS backend.

## API Usage

```python
from gradio_client import Client
client = Client("venomaru/suisei-tts")
result = client.predict(text="Hello!", language="English", emotion="neutral", api_name="/synthesize")
```

# cloudstream-cli

A CLI harness for anime streaming — built for AI agents and automation.

Search, browse, and stream anime from the command line with structured JSON output. Designed to be used by AI agents (Claude Code, OpenClaw, etc.) as a programmable interface to anime content.

## What makes this different?

Existing tools like `ani-cli` and `anipy-cli` are built for humans (interactive menus, arrow keys, pipe to mpv). This CLI is built for **agents**:

- **`--json` on every command** — structured output an LLM can parse
- **Session state** — chain operations across calls using indices (`search` → `load 1` → `links 3` → `play 1`)
- **Scraper registry** — pluggable providers, discoverable via `provider list`
- **No interactivity required** — every operation is a one-shot command

## Installation

```bash
git clone https://github.com/abdulyasir100/cloudstream-cli.git
cd cloudstream-cli
pip install -e .
```

Verify:
```bash
cli-anything-cloudstream --version
cli-anything-cloudstream status
```

## Quick Start

### Search for anime
```bash
cli-anything-cloudstream search "frieren"
```

### Load episode list
```bash
cli-anything-cloudstream load "https://anitaku.to/category/frieren-beyond-journeys-end-season-2"
# Or by search result index:
cli-anything-cloudstream load 3
```

### Get streaming links
```bash
cli-anything-cloudstream links "https://anitaku.to/frieren-beyond-journeys-end-season-2-episode-8"
# Or by episode index:
cli-anything-cloudstream links 8
```

### Watch in browser
```bash
cli-anything-cloudstream play 1
```

### Download (requires yt-dlp)
```bash
pip install yt-dlp
cli-anything-cloudstream download 1 -o "frieren-s2e08.mp4"
```

## JSON Mode (for agents)

Add `--json` before any command for machine-readable output:

```bash
cli-anything-cloudstream --json search "solo leveling"
```

```json
{
  "query": "solo leveling",
  "results": [
    {
      "name": "Solo Leveling",
      "url": "https://anitaku.to/category/solo-leveling",
      "api_name": "GogoAnime",
      "type": "Anime",
      "poster_url": "https://cdn.example.com/cover/solo-leveling.webp",
      "year": 2024
    }
  ],
  "total": 4
}
```

## Interactive REPL

Run without arguments to enter the interactive mode:

```bash
cli-anything-cloudstream
```

```
╭──────────────────────────────────────────────────────╮
│ ◆  cli-anything · Cloudstream                        │
│    v1.0.0                                            │
│                                                      │
│    Type help for commands, quit to exit               │
╰──────────────────────────────────────────────────────╯

◆ cloudstream ❯ search solo leveling
◆ cloudstream [solo leveling] ❯ load 1
◆ cloudstream [solo leveling] ❯ links 1
◆ cloudstream [solo leveling] ❯ play 1
◆ cloudstream [solo leveling] ❯ quit
```

## All Commands

| Command | Description |
|---------|-------------|
| `search <query>` | Search for anime across providers |
| `load <url\|#>` | Load content details + episode list |
| `links <url\|#>` | Extract streaming links for an episode |
| `play <url\|#>` | Open stream in browser |
| `download <url\|#> -o file` | Download stream via yt-dlp |
| `provider list` | List available scrapers |
| `extractor list` | List available extractors |
| `session info` | Show session info |
| `session history` | Show search/browse history |
| `session favorites` | Show favorited content |
| `session reset` | Reset session state |
| `status` | Check backend availability |

## Providers & Extractors

### Scrapers (Content Discovery)

| Provider | Domain | Content |
|----------|--------|---------|
| GogoAnime | `anitaku.to` | Anime (sub + dub) |
| HiAnime | `hianime.to` | Anime (sub + dub) |

### Extractors (Link Resolution)

| Extractor | Handles |
|-----------|---------|
| FileMoon | `filemoon.sx/to/in` embeds |
| StreamWish | `streamwish.to` + 20 mirror domains |
| Mp4Upload | `mp4upload.com` embeds |

## Agent Usage Example

A typical agent workflow:

```python
import subprocess, json

def cs(args):
    r = subprocess.run(
        ["cli-anything-cloudstream", "--json"] + args,
        capture_output=True, text=True
    )
    return json.loads(r.stdout)

# Search
results = cs(["search", "frieren"])
url = results["results"][0]["url"]

# Load episodes
content = cs(["load", url])
latest_ep = content["episodes"][0]["data"]

# Get streaming links
links = cs(["links", latest_ep])
stream_url = links["links"][0]["url"]

# Open in browser
subprocess.run(["cli-anything-cloudstream", "play", stream_url])
```

## Domain Updates

Anime sites rotate domains frequently. If searches return empty results, update the base URL:

- **GogoAnime**: Edit `cli_anything/cloudstream/core/scrapers/gogoanime.py` → `base_url`
  - Known mirrors: `anitaku.to`, `anitaku.so`, `gogoanime3.co`
- **HiAnime**: Edit `cli_anything/cloudstream/core/scrapers/hianime.py` → `base_url`
  - Known mirrors: `hianime.to`, `hianime.nz`, `aniwatch.to`

## Running Tests

```bash
python -m pytest cli_anything/cloudstream/tests/ -v
```

53 tests covering core modules, session persistence, and CLI subprocess validation.

## Project Structure

```
cli_anything/cloudstream/
├── cloudstream_cli.py          # Click CLI + REPL entry point
├── core/
│   ├── scrapers/               # Anime site scrapers
│   │   ├── base.py             # Base scraper + registry
│   │   ├── gogoanime.py        # GogoAnime scraper
│   │   └── hianime.py          # HiAnime scraper
│   ├── extractors/             # Embed URL resolvers
│   │   ├── base.py             # Base extractor + registry
│   │   ├── filemoon.py         # FileMoon extractor
│   │   ├── streamwish.py       # StreamWish extractor
│   │   ├── mp4upload.py        # Mp4Upload extractor
│   │   ├── js_unpacker.py      # Dean Edwards JS unpacker
│   │   └── crypto_helper.py    # AES-CBC decryption helpers
│   ├── provider.py             # Provider/extractor listing
│   ├── search.py               # Search data models
│   ├── content.py              # Content/episode data models
│   ├── stream.py               # Stream link data models
│   ├── download.py             # yt-dlp download integration
│   ├── session.py              # Persistent session state
│   └── export.py               # End-to-end pipeline
├── utils/
│   ├── cloudstream_backend.py  # Scraper engine + yt-dlp/ffmpeg
│   └── repl_skin.py            # Terminal UI skin
└── tests/
    ├── test_core.py            # 38 unit tests
    └── test_full_e2e.py        # 15 E2E + subprocess tests
```

## Credits

Built on top of [CloudStream](https://github.com/recloudstream/cloudstream)'s architecture patterns. The scraping techniques and extractor logic are ported from CloudStream's Kotlin source to Python.

## License

GPL-3.0 (same as CloudStream)

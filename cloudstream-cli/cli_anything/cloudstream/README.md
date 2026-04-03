# cli-anything-cloudstream

CLI harness for [CloudStream](https://github.com/recloudstream/cloudstream) — search, browse, and stream anime from the command line.

## Installation

```bash
cd cloudstream/agent-harness
pip install -e .
```

Verify:
```bash
cli-anything-cloudstream --version
cli-anything-cloudstream --help
```

### Optional: Download support

```bash
pip install yt-dlp
```

## Usage

### Interactive REPL (default)

```bash
cli-anything-cloudstream
```

```
◆ cloudstream ❯ search solo leveling
◆ cloudstream [solo leveling] ❯ load 1
◆ cloudstream [solo leveling] ❯ links 1
◆ cloudstream [solo leveling] ❯ play 1
◆ cloudstream [solo leveling] ❯ quit
```

### One-shot Commands

```bash
# Search
cli-anything-cloudstream search "frieren"

# Load episodes
cli-anything-cloudstream load "https://anitaku.to/category/frieren-beyond-journeys-end-season-2"

# Get streaming links
cli-anything-cloudstream links "https://anitaku.to/frieren-beyond-journeys-end-season-2-episode-8"

# Open in browser
cli-anything-cloudstream play "https://otakuhg.site/e/4c1izaguqajw"

# Download (requires yt-dlp)
cli-anything-cloudstream download "https://otakuhg.site/e/4c1izaguqajw" -o "frieren-s2e08.mp4"

# JSON output for scripts/agents
cli-anything-cloudstream --json search "naruto"
```

### Session

```bash
cli-anything-cloudstream session info
cli-anything-cloudstream session history
cli-anything-cloudstream session favorites
```

## Providers

| Provider | Domain | Content |
|----------|--------|---------|
| GogoAnime | `anitaku.to` | Anime (sub + dub) |
| HiAnime | `hianime.to` | Anime (sub + dub) |

## Extractors

| Extractor | Handles |
|-----------|---------|
| FileMoon | `filemoon.sx/to/in` embeds |
| StreamWish | `streamwish.to` + 20 mirrors |
| Mp4Upload | `mp4upload.com` embeds |

## Domain Updates

Anime sites rotate domains frequently. If searches return empty:

1. Edit `core/scrapers/gogoanime.py` → change `base_url`
2. Edit `core/scrapers/hianime.py` → change `base_url`

## Running Tests

```bash
cd cloudstream/agent-harness
python -m pytest cli_anything/cloudstream/tests/ -v
```

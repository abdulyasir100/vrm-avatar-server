# CloudStream CLI Harness — SOP

## Software Overview

**CloudStream** is an open-source Android streaming application that aggregates content
from multiple providers (web scrapers) into a unified interface. It supports movies,
TV series, anime, live streams, torrents, and more.

- **Repository**: https://github.com/recloudstream/cloudstream
- **Language**: Kotlin (Multiplatform — Android + JVM)
- **Version**: 4.6.2
- **License**: GPL-3.0

## Architecture Analysis

CloudStream's core is a **provider/extractor plugin architecture**. All actual streaming
providers are external plugins (`.cs3` Android DEX files). The library contains only
meta-providers (TMDB, Trakt) and 200+ embed extractors.

Since the plugin system is Android-only, this CLI uses **Python-native scrapers** that
replicate the same scraping techniques (requests + BeautifulSoup + AES crypto).

## Backend Strategy

### Python-Native Scrapers (Content Discovery)

| Scraper | Domain | Technique |
|---------|--------|-----------|
| GogoAnime | `anitaku.to` | HTML scraping + AJAX episode list |
| HiAnime | `hianime.to` | HTML scraping + AJAX JSON endpoints |

### Python-Native Extractors (Link Resolution)

| Extractor | Domains | Technique |
|-----------|---------|-----------|
| FileMoon | `filemoon.sx/to/in` | JS unpacking → M3U8 URL |
| StreamWish | `streamwish.to` + 20 mirrors | JS unpacking → M3U8 URL |
| Mp4Upload | `mp4upload.com` | JS unpacking → direct MP4 URL |

### yt-dlp (Stream Download)

For downloading M3U8/DASH/direct video streams to local files.

### ffmpeg (Media Processing)

Optional — for subtitle embedding and media muxing.

## CLI Commands

| Command | Purpose |
|---------|---------|
| `search <query>` | Search for anime across providers |
| `load <url\|#>` | Load content details + episode list |
| `links <url\|#>` | Extract streaming links for an episode |
| `play <url\|#>` | Open stream in browser |
| `download <url\|#>` | Download stream via yt-dlp |
| `provider list` | List available scrapers |
| `extractor list` | List available extractors |
| `session info/history/favorites` | Session management |
| `status` | Check backend component availability |

## Dependencies

### Python Dependencies (pip install)

- `click>=8.0.0` — CLI framework
- `prompt-toolkit>=3.0.0` — REPL interface
- `requests>=2.28.0` — HTTP client
- `beautifulsoup4>=4.11.0` — HTML parsing
- `pycryptodome>=3.15.0` — AES decryption for encrypted streams

### Optional

- **yt-dlp** — for `download` command
- **ffmpeg** — for subtitle muxing

## Domain Rotation

Anime sites change domains frequently. If a scraper stops working:

1. Check `core/scrapers/gogoanime.py` → update `base_url`
2. Check `core/scrapers/hianime.py` → update `base_url`
3. Known GogoAnime mirrors: `anitaku.to`, `anitaku.so`, `gogoanime3.co`
4. Known HiAnime mirrors: `hianime.to`, `hianime.nz`, `aniwatch.to`

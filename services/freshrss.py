"""FreshRSS Google Reader API client — fetches headlines for idle talk."""

import logging
import time
import random
import httpx
import config

logger = logging.getLogger(__name__)

_auth_token: str | None = None
_auth_expires: float = 0.0
_AUTH_TTL = 12 * 3600  # 12 hours

_articles_cache: list[dict] | None = None
_articles_cache_time: float = 0.0
_CACHE_TTL = 30 * 60  # 30 minutes

def _get_feed_keywords() -> list[str]:
    """Get feed keywords from character config (loaded at startup)."""
    import config
    return config.CHARACTER_FEED_KEYWORDS or []


async def _authenticate() -> str | None:
    """Authenticate via Google Reader API and cache the token."""
    global _auth_token, _auth_expires

    if _auth_token and time.time() < _auth_expires:
        return _auth_token

    if not config.FRESHRSS_API_PASSWORD:
        return None

    url = f"{config.FRESHRSS_URL}/api/greader.php/accounts/ClientLogin"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, data={
                "Email": config.FRESHRSS_USER,
                "Passwd": config.FRESHRSS_API_PASSWORD,
            })
            if resp.status_code != 200:
                logger.warning(f"[freshrss] Auth failed: {resp.status_code}")
                return None

            for line in resp.text.splitlines():
                if line.startswith("Auth="):
                    _auth_token = line[5:]
                    _auth_expires = time.time() + _AUTH_TTL
                    logger.info("[freshrss] Authenticated")
                    return _auth_token
    except Exception as e:
        logger.warning(f"[freshrss] Auth error: {e}")
    return None


async def fetch_recent_articles(count: int = 20) -> list[dict] | None:
    """Fetch recent articles from FreshRSS. Cached for 30 minutes."""
    global _articles_cache, _articles_cache_time

    if _articles_cache is not None and time.time() - _articles_cache_time < _CACHE_TTL:
        return _articles_cache

    token = await _authenticate()
    if not token:
        return None

    url = f"{config.FRESHRSS_URL}/api/greader.php/reader/api/0/stream/contents/reading-list"
    headers = {"Authorization": f"GoogleLogin auth={token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params={"n": count}, headers=headers)
            if resp.status_code == 401:
                global _auth_token, _auth_expires
                _auth_token = None
                _auth_expires = 0.0
                token = await _authenticate()
                if not token:
                    return None
                headers = {"Authorization": f"GoogleLogin auth={token}"}
                resp = await client.get(url, params={"n": count}, headers=headers)

            if resp.status_code != 200:
                logger.warning(f"[freshrss] Fetch failed: {resp.status_code}")
                return None

            data = resp.json()
            articles = []
            for item in data.get("items", []):
                source = item.get("origin", {}).get("title", "Unknown")
                title = item.get("title", "")
                if title:
                    is_character_post = any(kw in source.lower() for kw in _get_feed_keywords())
                    article_id = item.get("id", title)
                    articles.append({
                        "id": article_id,
                        "title": title,
                        "source": source,
                        "is_character_post": is_character_post,
                    })

            _articles_cache = articles
            _articles_cache_time = time.time()
            logger.info(f"[freshrss] Cached {len(articles)} articles")
            return articles
    except Exception as e:
        logger.warning(f"[freshrss] Fetch error: {e}")
    return None


async def get_random_headline() -> dict | None:
    """Get a random headline. Returns {"title", "source", "is_character_post"} or None."""
    articles = await fetch_recent_articles()
    if not articles:
        return None
    return random.choice(articles)


async def get_new_character_posts(seen_titles: set[str]) -> list[dict]:
    """Return character's posts that haven't been seen yet.

    Returns list of {"title", "source", "is_character_post": True} dicts.
    """
    articles = await fetch_recent_articles()
    if not articles:
        return []
    return [a for a in articles if a["is_character_post"] and a.get("id", a["title"]) not in seen_titles]


async def check_health() -> str:
    """Health check — can we authenticate to FreshRSS?"""
    if not config.FRESHRSS_ENABLED:
        return "disabled"
    token = await _authenticate()
    return "ok" if token else "offline"

"""Search operations — search for content across providers.

Wraps CloudStream's search() provider method, outputting results
in a structured format for CLI and JSON consumption.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class SearchResult:
    """A single search result from a provider."""
    name: str
    url: str
    api_name: str
    type: str = "Movie"
    poster_url: str | None = None
    year: int | None = None
    quality: str | None = None
    score: float | None = None
    dub_status: list[str] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "SearchResult":
        return cls(
            name=data.get("name", ""),
            url=data.get("url", ""),
            api_name=data.get("api_name", data.get("apiName", "")),
            type=data.get("type", "Movie"),
            poster_url=data.get("poster_url", data.get("posterUrl")),
            year=data.get("year"),
            quality=data.get("quality"),
            score=data.get("score"),
            dub_status=data.get("dub_status", data.get("dubStatus")),
        )


@dataclass
class SearchResults:
    """Collection of search results with metadata."""
    query: str
    results: list[SearchResult] = field(default_factory=list)
    has_next: bool = False
    provider: str | None = None
    total: int = 0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "has_next": self.has_next,
            "provider": self.provider,
            "total": self.total or len(self.results),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SearchResults":
        results = [SearchResult.from_dict(r) for r in data.get("results", [])]
        return cls(
            query=data.get("query", ""),
            results=results,
            has_next=data.get("has_next", data.get("hasNext", False)),
            provider=data.get("provider"),
            total=data.get("total", len(results)),
        )


def search(query: str, providers: list[str] | None = None,
           page: int = 1) -> SearchResults:
    """Search for content across providers.

    Args:
        query: Search query string.
        providers: Optional list of provider names to search.
        page: Page number for pagination.

    Returns:
        SearchResults with matched content.

    Raises:
        RuntimeError: If backend is unavailable.
    """
    from cli_anything.cloudstream.utils.cloudstream_backend import scraper_search

    result = scraper_search(query, providers)

    if "error" in result:
        raise RuntimeError(result["error"])

    return SearchResults.from_dict({
        "query": query,
        **result,
    })


def format_results_table(results: SearchResults) -> tuple[list[str], list[list[str]]]:
    """Format search results as table headers and rows.

    Returns:
        Tuple of (headers, rows) for table display.
    """
    headers = ["#", "Name", "Type", "Year", "Quality", "Provider"]
    rows = []
    for i, r in enumerate(results.results, 1):
        rows.append([
            str(i),
            r.name[:40],
            r.type,
            str(r.year) if r.year else "-",
            r.quality or "-",
            r.api_name[:15],
        ])
    return headers, rows

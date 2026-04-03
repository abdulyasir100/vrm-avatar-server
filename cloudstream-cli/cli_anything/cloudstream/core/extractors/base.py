"""Base extractor class and registry."""

from cli_anything.cloudstream.core.scrapers.base import StreamItem, SubtitleItem


class ExtractorBase:
    """Base class for embed URL extractors."""

    name: str = "Unknown"
    main_url: str = ""
    requires_referer: bool = False

    def can_handle(self, url: str) -> bool:
        """Check if this extractor can handle the given URL."""
        return self.main_url.replace("https://", "").replace("http://", "") in url

    def extract(self, url: str, referer: str | None = None) -> tuple[list[StreamItem], list[SubtitleItem]]:
        """Extract stream links from embed URL. Override in subclass."""
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "main_url": self.main_url,
            "requires_referer": self.requires_referer,
        }


class ExtractorRegistry:
    """Registry of available extractors."""

    _extractors: list[ExtractorBase] = []

    @classmethod
    def register(cls, extractor: ExtractorBase):
        cls._extractors.append(extractor)

    @classmethod
    def all(cls) -> list[ExtractorBase]:
        return list(cls._extractors)

    @classmethod
    def find(cls, url: str) -> ExtractorBase | None:
        """Find an extractor that can handle the given URL."""
        for ext in cls._extractors:
            if ext.can_handle(url):
                return ext
        return None

    @classmethod
    def extract(cls, url: str, referer: str | None = None) -> tuple[list[StreamItem], list[SubtitleItem]]:
        """Extract using the first matching extractor."""
        ext = cls.find(url)
        if ext:
            return ext.extract(url, referer)
        return [], []

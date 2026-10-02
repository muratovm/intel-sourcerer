"""Load publisher index pages and optional RSS endpoints from TOML."""
from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from .feeds import FeedSource


@dataclass(frozen=True)
class PublisherSource:
    id: str
    name: str
    parent_url: str
    source_type: str
    rss_url: str | None = None
    legacy_parent_url: str | None = None
    notes: str = ""
    enabled: bool = True

    def as_feed(self) -> FeedSource | None:
        return FeedSource(self.name, self.rss_url) if self.enabled and self.rss_url else None


def load_sources(path: str | Path) -> list[PublisherSource]:
    """Validate the complete config before returning any source."""
    with Path(path).open("rb") as handle:
        config = tomllib.load(handle)
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported sources config schema_version; expected 1")
    records = config.get("sources")
    if not isinstance(records, list) or not records:
        raise ValueError("Config must contain at least one [[sources]] entry")
    sources = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each source must be a TOML table")
        try:
            source = PublisherSource(**record)
        except TypeError as exc:
            raise ValueError(f"Invalid source fields: {exc}") from exc
        for key in ("id", "name", "parent_url", "source_type"):
            value = getattr(source, key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Source {key} must be a nonempty string")
        if not isinstance(source.enabled, bool) or not isinstance(source.notes, str):
            raise ValueError("Source enabled must be boolean and notes must be a string")
        if source.id in seen:
            raise ValueError(f"Duplicate source id: {source.id}")
        seen.add(source.id)
        for key in ("parent_url", "rss_url", "legacy_parent_url"):
            value = getattr(source, key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{source.id}: {key} must be a URL string")
            try:
                url = urlsplit(value)
                valid = url.scheme in {"http", "https"} and url.hostname
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{source.id}: {key} must be an HTTP(S) URL")
        sources.append(source)
    return sources

"""Load publisher index pages and optional RSS endpoints from TOML."""
from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from .feeds import FeedSource


DEFAULT_INCLUDE_KEYWORDS = (
    "cyber",
    "distillation",
    "malicious",
    "misuse",
    "espionage",
    "phishing",
    "ransomware",
    "vulnerabilit",
    "prompt injection",
    "hacking",
    "frontier safeguards",
    "cve-",
    "exploit",
    "malware",
    "backdoor",
    "intrusion",
    "zero-day",
    "adversar",
    "supply chain",
    "threat actor",
    "botnet",
    "rootkit",
    "infostealer",
    "post-exploitation",
)

DEFAULT_EXCLUDE_KEYWORDS = (
    "devday",
    "named a leader",
    "gartner",
    "forrester",
    "now available in aws",
    "a new generation",
)


def _clean_keywords(values, *, max_items=120):
    if not isinstance(values, (list, tuple)):
        raise ValueError("Keywords must be a list of strings.")
    cleaned = []
    seen = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("Each keyword must be a string.")
        term = " ".join(raw.split()).strip()
        if not term:
            continue
        if len(term) < 2 or len(term) > 80:
            raise ValueError("Each keyword must be 2–80 characters.")
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(term)
        if len(cleaned) >= max_items:
            break
    return cleaned


def load_filters(path: str | Path) -> dict:
    """Read custom include/exclude keyword lists from [filters] in sources.toml, falling back to built-in defaults."""
    with Path(path).open("rb") as handle:
        config = tomllib.load(handle)
    table = config.get("filters")
    if isinstance(table, dict) and ("include_keywords" in table or "exclude_keywords" in table):
        inc = _clean_keywords(table.get("include_keywords", list(DEFAULT_INCLUDE_KEYWORDS)))
        exc = _clean_keywords(table.get("exclude_keywords", list(DEFAULT_EXCLUDE_KEYWORDS)))
        return {
            "include_keywords": inc,
            "exclude_keywords": exc,
            "is_custom": True,
            "defaults": {
                "include_keywords": list(DEFAULT_INCLUDE_KEYWORDS),
                "exclude_keywords": list(DEFAULT_EXCLUDE_KEYWORDS),
            },
        }
    return {
        "include_keywords": list(DEFAULT_INCLUDE_KEYWORDS),
        "exclude_keywords": list(DEFAULT_EXCLUDE_KEYWORDS),
        "is_custom": False,
        "defaults": {
            "include_keywords": list(DEFAULT_INCLUDE_KEYWORDS),
            "exclude_keywords": list(DEFAULT_EXCLUDE_KEYWORDS),
        },
    }


def _write_filters_block(path: str | Path, block: str):
    import os
    import re
    import tempfile

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    # Remove any existing top-level [filters] table (up to the next [section] / [[sources]] header).
    stripped = re.sub(r"(?ms)^\[filters\]\s*\n.*?(?=^\[|\Z)", "", text)
    if block:
        first_source = re.search(r"(?m)^\[\[sources\]\]", stripped)
        if first_source:
            idx = first_source.start()
            updated_text = stripped[:idx].rstrip() + "\n\n" + block + "\n" + stripped[idx:]
        else:
            updated_text = stripped.rstrip() + "\n\n" + block
    else:
        updated_text = re.sub(r"\n{3,}", "\n\n", stripped)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(updated_text)
    try:
        load_sources(temporary)
        load_filters(temporary)
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_filters(path: str | Path, include_keywords, exclude_keywords) -> dict:
    """Persist custom include/exclude keywords to [filters] in sources.toml atomically."""
    inc = _clean_keywords(include_keywords)
    exc = _clean_keywords(exclude_keywords)
    if not inc:
        raise ValueError("Keep at least one include keyword (or switch sources to 'Keep all posts').")
    inc_toml = ", ".join(_toml_quote(term) for term in inc)
    exc_toml = ", ".join(_toml_quote(term) for term in exc)
    block = (
        "[filters]\n"
        f"include_keywords = [{inc_toml}]\n"
        f"exclude_keywords = [{exc_toml}]\n"
    )
    _write_filters_block(path, block)
    return load_filters(path)


def reset_filters(path: str | Path) -> dict:
    """Remove custom [filters] from sources.toml to restore built-in defaults."""
    _write_filters_block(path, "")
    return load_filters(path)


@dataclass(frozen=True)
class PublisherSource:
    id: str
    name: str
    parent_url: str
    source_type: str
    rss_url: str | None = None
    # Still read so existing personal source files load, but not shown: it can redirect to parent_url.
    legacy_parent_url: str | None = None
    notes: str = ""
    enabled: bool = True
    relevance: str = "auto"
    # Category or archive pages read alongside the feed, for publishers whose feed only holds the latest few posts.
    listing_urls: tuple[str, ...] = ()

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
        listing = record.get("listing_urls", ())
        if not isinstance(listing, (list, tuple)) or not all(isinstance(url, str) for url in listing):
            raise ValueError("Source listing_urls must be a list of URL strings")
        record = {**record, "listing_urls": tuple(listing)}
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
        if source.relevance not in {"auto", "all"}:
            raise ValueError("Source relevance must be 'auto' or 'all'")
        if source.id in seen:
            raise ValueError(f"Duplicate source id: {source.id}")
        seen.add(source.id)
        for key, value in [(key, getattr(source, key)) for key in ("parent_url", "rss_url", "legacy_parent_url")] + [
            ("listing_urls", url) for url in source.listing_urls
        ]:
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


def set_source_enabled(path, source_id, enabled):
    """Update one source while preserving TOML comments and ordering."""
    import re
    import os
    import tempfile
    path=Path(path)
    if type(enabled) is not bool:
        raise ValueError('Enabled must be boolean')
    sources=load_sources(path)
    if source_id not in {source.id for source in sources}:
        raise ValueError('Unknown source')
    text=path.read_text()
    parts=re.split(r'(?m)(?=^\[\[sources\]\]\s*$)',text)
    for index,part in enumerate(parts):
        match=re.search(r'(?m)^id\s*=\s*[\"\']([^\"\']+)[\"\']',part)
        if match and match.group(1)==source_id:
            line=f'enabled = {str(enabled).lower()}'
            if re.search(r'(?m)^enabled\s*=',part):
                part=re.sub(r'(?m)^enabled\s*=\s*(true|false)',line,part)
            else:
                part=part.replace(match.group(0),match.group(0)+'\n'+line,1)
            parts[index]=part
            break
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as handle:
        temporary=Path(handle.name)
        handle.write(''.join(parts))
    try:
        load_sources(temporary)
        os.chmod(temporary,path.stat().st_mode)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{escaped}"'


def _slugify(name: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return f"custom-{slug or 'feed'}"


def add_custom_source(path, name, rss_url, parent_url=None, notes=""):
    """Append a validated custom RSS/Atom feed source to the user's TOML config."""
    import os
    import tempfile

    path = Path(path)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("Enter a source name (1-80 characters).")
    name = " ".join(name.split())
    if not isinstance(rss_url, str) or not rss_url.strip():
        raise ValueError("Enter an HTTP or HTTPS RSS/Atom feed URL.")
    rss_url = rss_url.strip()
    parsed = urlsplit(rss_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Feed URL must be a valid HTTP or HTTPS address.")
    if parent_url and isinstance(parent_url, str) and parent_url.strip():
        parent_url = parent_url.strip()
        p_parsed = urlsplit(parent_url)
        if p_parsed.scheme not in {"http", "https"} or not p_parsed.hostname:
            raise ValueError("Site URL must be a valid HTTP or HTTPS address.")
    else:
        parent_url = f"{parsed.scheme}://{parsed.netloc}/"
    notes = " ".join((notes or "").split())[:200]

    existing = load_sources(path)
    if any(src.rss_url and src.rss_url.rstrip("/") == rss_url.rstrip("/") for src in existing):
        raise ValueError("That feed URL is already configured.")
    taken = {src.id for src in existing}
    base_id = _slugify(name)
    source_id = base_id
    suffix = 2
    while source_id in taken:
        source_id = f"{base_id}-{suffix}"
        suffix += 1

    block = (
        f"\n[[sources]]\n"
        f"id = {_toml_quote(source_id)}\n"
        f"name = {_toml_quote(name)}\n"
        f'source_type = "custom"\n'
        f"enabled = true\n"
        f"parent_url = {_toml_quote(parent_url)}\n"
        f"rss_url = {_toml_quote(rss_url)}\n"
        f"notes = {_toml_quote(notes or 'Custom RSS/Atom feed added by user.')}\n"
    )
    text = path.read_text(encoding="utf-8")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text.rstrip() + "\n" + block)
    try:
        updated = load_sources(temporary)
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return next(src for src in updated if src.id == source_id)


def remove_custom_source(path, source_id):
    """Remove a user-added custom source while preserving built-in sources and TOML comments."""
    import os
    import re
    import tempfile

    path = Path(path)
    sources = load_sources(path)
    target = next((src for src in sources if src.id == source_id), None)
    if target is None:
        raise ValueError("Unknown source.")
    if target.source_type != "custom":
        raise ValueError("Only custom sources can be removed; built-in sources can be disabled instead.")
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)(?=^\[\[sources\]\]\s*$)", text)
    kept = []
    for part in parts:
        match = re.search(r'(?m)^id\s*=\s*["\']([^"\']+)["\']', part)
        if match and match.group(1) == source_id:
            continue
        kept.append(part)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write("".join(kept))
    try:
        load_sources(temporary)
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def set_source_relevance(path, source_id, relevance):
    """Update a source's relevance mode ('auto' keyword filter or 'all' posts) atomically."""
    import os
    import re
    import tempfile

    path = Path(path)
    if relevance not in {"auto", "all"}:
        raise ValueError("Relevance must be 'auto' or 'all'.")
    sources = load_sources(path)
    if source_id not in {source.id for source in sources}:
        raise ValueError("Unknown source.")
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)(?=^\[\[sources\]\]\s*$)", text)
    for index, part in enumerate(parts):
        match = re.search(r'(?m)^id\s*=\s*["\']([^"\']+)["\']', part)
        if match and match.group(1) == source_id:
            line = f'relevance = "{relevance}"'
            if re.search(r"(?m)^relevance\s*=", part):
                part = re.sub(r'(?m)^relevance\s*=\s*["\'][^"\']*["\']', line, part)
            else:
                part = part.replace(match.group(0), match.group(0) + "\n" + line, 1)
            parts[index] = part
            break
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write("".join(parts))
    try:
        load_sources(temporary)
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

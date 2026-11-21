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
    # Still read so existing personal source files load, but not shown: it can redirect to parent_url.
    legacy_parent_url: str | None = None
    notes: str = ""
    enabled: bool = True
    relevance: str = "auto"

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
        if source.relevance not in {"auto", "all"}:
            raise ValueError("Source relevance must be 'auto' or 'all'")
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

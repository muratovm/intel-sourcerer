"""Daily SQLite snapshots before edits, independent of Git."""
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
import re
import sqlite3
import threading

from .storage import BUSY_TIMEOUT

_lock=threading.Lock()
KEEP_DAILY=3


def prune_backups(database, keep=KEEP_DAILY):
    """Remove all but the newest daily copies. Named snapshots (anything not stem-YYYY-MM-DD) are left alone."""
    path=Path(database)
    daily=sorted(f for f in (path.parent/'backups').glob(f'{path.stem}-*.sqlite3')
                 if re.fullmatch(re.escape(path.stem)+r'-\d{4}-\d{2}-\d{2}\.sqlite3', f.name))
    for old in daily[:-keep] if keep else daily:
        old.unlink(missing_ok=True)


def _snapshot_file(path, *, overwrite=False):
    destination=path.parent/'backups'/f'{path.stem}-{datetime.now(timezone.utc):%Y-%m-%d}.sqlite3'
    if destination.exists() and not overwrite:
        return destination
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix('.tmp')
    try:
        with closing(sqlite3.connect(path, timeout=BUSY_TIMEOUT)) as source, closing(sqlite3.connect(temporary)) as target:
            source.backup(target)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    prune_backups(path)
    return destination


def backup_database(database, *, overwrite=False):
    path=Path(database)
    with _lock:
        destination = _snapshot_file(path, overwrite=overwrite)
        accounts_db = path.parent / 'accounts.sqlite3'
        if path.name != 'accounts.sqlite3' and accounts_db.is_file():
            _snapshot_file(accounts_db, overwrite=overwrite)
    return destination


def backup_summary(database):
    """Count, total size, and latest snapshot date for this library's backups."""
    path = Path(database).resolve()
    folder = path.parent / 'backups'
    files = sorted(f for f in folder.glob(f'{path.stem}-*.sqlite3') if f.is_file()) if folder.is_dir() else []
    latest = ""
    if files:
        match = re.search(r'(\d{4}-\d{2}-\d{2})\.sqlite3$', files[-1].name)
        latest = match.group(1) if match else datetime.fromtimestamp(files[-1].stat().st_mtime, timezone.utc).strftime('%Y-%m-%d')
    return {
        "count": len(files),
        "latest": latest,
        "total_bytes": sum(f.stat().st_size for f in files),
    }


def snapshot_bytes(database):
    """Return a consistent SQLite snapshot as bytes without blocking WAL writers."""
    import tempfile

    path = Path(database).resolve()
    with tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with closing(sqlite3.connect(path, timeout=BUSY_TIMEOUT)) as source, closing(sqlite3.connect(tmp_path)) as target:
            source.backup(target)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def _safe_slug(name, fallback="item"):
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    return (slug[:60] or fallback)


def export_notes_zip(db):
    """Build an in-memory zip archive of all noted/highlighted articles and all topics as Markdown."""
    import io
    import zipfile
    from .cti_export import export_topic_format
    from .library import export_article
    from .sessions import has_notes

    buf = io.BytesIO()
    article_count = 0
    topic_count = 0
    used_names = set()

    def unique_path(prefix, title, fallback):
        base = _safe_slug(title, fallback)
        candidate = f"{prefix}/{base}.md"
        idx = 2
        while candidate in used_names:
            candidate = f"{prefix}/{base}-{idx}.md"
            idx += 1
        used_names.add(candidate)
        return candidate

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for row in db.execute(
            """SELECT a.url, a.title, a.notes, a.notes_format,
                      EXISTS(SELECT 1 FROM article_highlights h WHERE h.article_url=a.url) AS has_hl
               FROM articles a ORDER BY julianday(a.published_at) DESC, a.title COLLATE NOCASE"""
        ).fetchall():
            if not (has_notes(row["notes"], row["notes_format"]) or row["has_hl"]):
                continue
            md = export_article(db, row["url"])
            if md:
                zf.writestr(unique_path("articles", row["title"], "article"), md)
                article_count += 1
        for trow in db.execute("SELECT id, name FROM topics ORDER BY name COLLATE NOCASE").fetchall():
            exported = export_topic_format(db, trow["id"], "markdown")
            if exported:
                body, _ctype, _ext, name = exported
                zf.writestr(unique_path("topics", name, "topic"), body)
                topic_count += 1
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        zf.writestr(
            "README.txt",
            f"Intel Sourcerer notes & topics export ({stamp})\n"
            f"- Noted/highlighted articles: {article_count}\n"
            f"- Topics: {topic_count}\n",
        )
    return buf.getvalue()

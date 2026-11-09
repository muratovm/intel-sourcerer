"""Daily SQLite snapshots before edits, independent of Git."""
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


def backup_database(database):
    path=Path(database)
    destination=path.parent/'backups'/f'{path.stem}-{datetime.now(timezone.utc):%Y-%m-%d}.sqlite3'
    with _lock:
        if destination.exists():return destination
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=destination.with_suffix('.tmp')
        try:
            with sqlite3.connect(path, timeout=BUSY_TIMEOUT) as source, sqlite3.connect(temporary) as target:
                source.backup(target)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        prune_backups(path)
    return destination

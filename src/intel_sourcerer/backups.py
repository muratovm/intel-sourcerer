"""Daily SQLite snapshots before edits, independent of Git."""
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import threading

_lock=threading.Lock()
def backup_database(database):
    path=Path(database)
    destination=path.parent/'backups'/f'{path.stem}-{datetime.now(timezone.utc):%Y-%m-%d}.sqlite3'
    with _lock:
        if destination.exists():return destination
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=destination.with_suffix('.tmp')
        try:
            with sqlite3.connect(path) as source, sqlite3.connect(temporary) as target:
                source.backup(target)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination

"""Keeps the search log from growing without end.

A log past MAX_BYTES is compressed into job_finder.log.1.gz when the next run starts, and the log begins again; the
three newest archives are kept. Nothing reads the old runs except people, and a gzip file opens in any archive tool.
"""

import gzip
import shutil
from pathlib import Path

MAX_BYTES = 2_000_000
KEEP = 3


def _archive(path, number):
    return path.with_name(f"{path.name}.{number}.gz")


def rotate(path, max_bytes=None, keep=KEEP):
    """Archive the log when it is over max_bytes (default MAX_BYTES). Returns True when it did. Never raises: a log that
    cannot be archived (open somewhere else, disk full) is simply left as it is."""
    path = Path(path)
    limit = MAX_BYTES if max_bytes is None else max_bytes
    try:
        if not path.exists() or path.stat().st_size <= limit:
            return False
        _archive(path, keep).unlink(missing_ok=True)
        for number in range(keep - 1, 0, -1):
            if _archive(path, number).exists():
                _archive(path, number).replace(_archive(path, number + 1))
        with path.open("rb") as source, gzip.open(_archive(path, 1), "wb") as target:
            shutil.copyfileobj(source, target)
        path.write_bytes(b"")
        return True
    except OSError:
        return False

"""Read-only access to the provided source dataset (``data/data.sqlite``).

The dataset (users, events, features) is treated as immutable input — we open it read-only and never
write to it. All derived state lives in our own app database (see ``db.py``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_source(path: str) -> sqlite3.Connection:
    """Open the provided dataset strictly read-only."""
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Source dataset not found at {path!r}. Expected the provided data/data.sqlite."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

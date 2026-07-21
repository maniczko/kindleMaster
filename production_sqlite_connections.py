from __future__ import annotations

import sqlite3
from typing import Any

from durable_job_queue import DurableJobDatabase


class ClosingSQLiteConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection, then always close the descriptor."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def install_closing_sqlite_connections() -> None:
    current = DurableJobDatabase.connect
    if getattr(current, "_kindlemaster_closing_connection", False):
        return

    def connect(self: DurableJobDatabase) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
            factory=ClosingSQLiteConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    connect._kindlemaster_closing_connection = True
    connect._kindlemaster_original_connect = current
    DurableJobDatabase.connect = connect

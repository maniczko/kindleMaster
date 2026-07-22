from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from durable_job_queue import DurableJobDatabase
from production_sqlite_connections import install_closing_sqlite_connections


class ProductionSQLiteConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        install_closing_sqlite_connections()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = DurableJobDatabase(Path(self.temp_dir.name) / "runtime.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_successful_context_commits_and_closes_connection(self) -> None:
        with self.database.connect() as connection:
            connection.execute("CREATE TABLE probe(value TEXT)")
            connection.execute("INSERT INTO probe(value) VALUES ('stored')")

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with self.database.connect() as verification:
            row = verification.execute("SELECT value FROM probe").fetchone()
        self.assertEqual(row["value"], "stored")

    def test_failed_context_rolls_back_and_closes_connection(self) -> None:
        with self.database.connect() as setup:
            setup.execute("CREATE TABLE probe(value TEXT)")

        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO probe(value) VALUES ('not-stored')")
                raise RuntimeError("rollback")

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with self.database.connect() as verification:
            count = verification.execute("SELECT COUNT(*) AS count FROM probe").fetchone()["count"]
        self.assertEqual(count, 0)

    def test_many_contexts_do_not_leave_database_locked(self) -> None:
        with self.database.connect() as setup:
            setup.execute("CREATE TABLE counter(value INTEGER)")
            setup.execute("INSERT INTO counter(value) VALUES (0)")

        for _ in range(200):
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("UPDATE counter SET value = value + 1")
                connection.commit()

        with self.database.connect() as verification:
            value = verification.execute("SELECT value FROM counter").fetchone()["value"]
        self.assertEqual(value, 200)


if __name__ == "__main__":
    unittest.main()

"""Migrations on throwaway SQLite databases: fresh install, existing database, rollback, no drift."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db import Base

BACKEND = Path(__file__).resolve().parents[1]


def alembic(url: str, *arguments: str) -> None:
    env = {**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(BACKEND), "JWT_SECRET": "x" * 40}
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), *arguments],
        env=env,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-1500:]


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.mkdtemp(prefix="ryze-migrate-")
        self.url = f"sqlite:///{directory}/test.db"
        self.engine = sa.create_engine(self.url)
        self.addCleanup(self.engine.dispose)

    def tables(self) -> set[str]:
        return set(sa.inspect(self.engine).get_table_names())

    def columns(self, table: str) -> set[str]:
        return {column["name"] for column in sa.inspect(self.engine).get_columns(table)}

    def test_a_fresh_database_matches_the_models(self) -> None:
        alembic(self.url, "upgrade", "head")
        self.assertIn("conversation_query_states", self.tables())
        with self.engine.connect() as connection:
            drift = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        self.assertEqual([item for item in drift if "alembic_version" not in str(item)], [])

    def test_an_existing_database_keeps_its_data_and_gains_the_new_objects(self) -> None:
        tables = [table for name, table in Base.metadata.tables.items() if name != "conversation_query_states"]
        Base.metadata.create_all(self.engine, tables=tables)
        with self.engine.begin() as connection:
            connection.execute(sa.text("ALTER TABLE knowledge_chunks DROP COLUMN embedding"))
            connection.execute(sa.text("INSERT INTO conversations (id, username, domain, title, created_at, updated_at) VALUES ('c1', 'u', 'banking', 't', '2026-01-01', '2026-01-01')"))
        alembic(self.url, "stamp", "0001")
        alembic(self.url, "upgrade", "head")
        self.assertIn("embedding", self.columns("knowledge_chunks"))
        self.assertIn("conversation_query_states", self.tables())
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(sa.text("SELECT COUNT(*) FROM conversations")).scalar(), 1)

    def test_it_can_run_after_startup_has_already_made_the_changes(self) -> None:
        Base.metadata.create_all(self.engine)
        alembic(self.url, "stamp", "0001")
        alembic(self.url, "upgrade", "head")
        self.assertIn("conversation_query_states", self.tables())

    def test_the_banking_revision_rolls_back(self) -> None:
        alembic(self.url, "upgrade", "head")
        alembic(self.url, "downgrade", "0001")
        self.assertNotIn("conversation_query_states", self.tables())
        self.assertNotIn("embedding", self.columns("knowledge_chunks"))
        self.assertIn("conversations", self.tables())


if __name__ == "__main__":
    unittest.main()

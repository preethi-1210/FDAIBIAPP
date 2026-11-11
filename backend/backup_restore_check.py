"""Prove the PostgreSQL backup can be restored: dump, restore into a new database, compare, clean up.

Only reads the live database. The check database has a new, timestamped name and is the only one
dropped; the dump file is deleted unless --keep is given, because it holds application data.
    python backup_restore_check.py --bin "C:/Program Files/PostgreSQL/18/bin" --workdir <temp dir>
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.config import settings


def table_counts(url) -> dict[str, int]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            names = sa.inspect(connection).get_table_names()
            return {name: connection.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar() for name in sorted(names)}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bin", default="", help="Directory holding pg_dump, pg_restore, createdb and dropdb")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--keep", action="store_true", help="Keep the dump file")
    parser.add_argument("--test-migrations", action="store_true", help="Also run the migrations on the restored copy")
    args = parser.parse_args()
    url = make_url(settings.database_url)
    assert url.get_backend_name() == "postgresql", "This check is for PostgreSQL."
    check_name = f"ryze_restore_check_{int(time.time())}"
    assert check_name != url.database
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    connection = ["-h", url.host or "localhost", "-p", str(url.port or 5432), "-U", url.username or "postgres"]
    tool = lambda name: str(Path(args.bin) / name) if args.bin else name  # noqa: E731
    dump = Path(args.workdir) / f"{check_name}.backup"

    def run(*command: str) -> None:
        subprocess.run(command, env=env, check=True, capture_output=True, text=True, timeout=600)

    started = time.perf_counter()
    before = table_counts(url)
    run(tool("pg_dump"), *connection, "-Fc", "-f", str(dump), url.database)
    dump_seconds = time.perf_counter() - started
    created = False
    try:
        run(tool("createdb"), *connection, check_name)
        created = True
        started = time.perf_counter()
        run(tool("pg_restore"), *connection, "--no-owner", "-d", check_name, str(dump))
        restore_seconds = time.perf_counter() - started
        restored_url = url.set(database=check_name)
        after = table_counts(restored_url)
        differences = {name: (before.get(name), after.get(name)) for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)}
        migrated = None
        if args.test_migrations:
            # A real copy of the data is the honest place to prove the migrations are safe.
            migration_env = {**env, "DATABASE_URL": restored_url.render_as_string(hide_password=False)}
            for arguments in (("stamp", "0001"), ("upgrade", "head")):
                subprocess.run([sys.executable, "-m", "alembic", *arguments], env=migration_env, check=True, capture_output=True, text=True, timeout=300)
            migrated_counts = table_counts(restored_url)
            migrated = {
                "rows_unchanged": all(migrated_counts.get(name) == count for name, count in after.items()),
                "new_tables": sorted(set(migrated_counts) - set(after)),
            }
        print(json.dumps({
            "tables": len(before),
            "rows": sum(before.values()),
            "dump_bytes": dump.stat().st_size,
            "dump_seconds": round(dump_seconds, 2),
            "restore_seconds": round(restore_seconds, 2),
            "differences": differences,
            "restore_verified": not differences,
            "migrations_on_restored_copy": migrated,
        }, indent=2))
    finally:
        if created:
            run(tool("dropdb"), *connection, check_name)
        if not args.keep:
            dump.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

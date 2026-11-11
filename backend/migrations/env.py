"""Alembic environment. Uses the application's own settings and table metadata."""

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.config import settings
from app.db import Base

target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connectable = create_engine(settings.database_url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()

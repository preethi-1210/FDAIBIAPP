import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(
    settings.database_url,
    future=True,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "before_cursor_execute")
def _start_query_timer(_connection, _cursor, _statement, _parameters, context, _executemany):
    context._ryze_query_started = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _log_query_latency(_connection, _cursor, _statement, _parameters, context, _executemany):
    started = getattr(context, "_ryze_query_started", None)
    if started is not None:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        log = logger.warning if elapsed_ms >= settings.slow_query_ms else logger.debug
        log(
            "Database query completed in %.0f ms: %s",
            elapsed_ms,
            " ".join(_statement.split())[:160],
            extra={"database_query_latency_ms": elapsed_ms},
        )


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

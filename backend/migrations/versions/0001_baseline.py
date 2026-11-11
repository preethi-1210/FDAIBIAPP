"""Baseline: the schema as it stood when Alembic was adopted.

An existing database needs no change: run `alembic stamp 0001`, then `alembic upgrade head`. On an empty
database this creates the tables, as the application's start-up does.
"""

from alembic import op

from app import models  # noqa: F401
from app.db import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Added by later revisions.
_LATER_TABLES = {
    "conversation_query_states",
    "subscription_plans",
    "plan_features",
    "user_subscriptions",
    "usage_periods",
    "usage_transactions",
    "llm_usage_events",
    "usage_notifications",
    "subscription_history",
    "payment_transactions",
}


def upgrade() -> None:
    bind = op.get_bind()
    tables = [table for name, table in Base.metadata.tables.items() if name not in _LATER_TABLES]
    Base.metadata.create_all(bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError("The baseline is not reversible; restore from a backup instead.")

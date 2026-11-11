"""Banking workflow: follow-up state and knowledge embeddings. Additive and safe to re-run.

Application start-up applies the same changes through ensure_schema, so either may have run first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "conversation_query_states" not in inspector.get_table_names():
        op.create_table(
            "conversation_query_states",
            sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("plan_json", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "embedding" not in {column["name"] for column in inspector.get_columns("knowledge_chunks")}:
        with op.batch_alter_table("knowledge_chunks") as batch:
            batch.add_column(sa.Column("embedding", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_chunks") as batch:
        batch.drop_column("embedding")
    op.drop_table("conversation_query_states")

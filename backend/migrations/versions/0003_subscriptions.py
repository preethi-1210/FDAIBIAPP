"""Subscription plans, AI allowances, provider usage and payments. Additive.

Application start-up creates the same tables through ensure_schema, so either may have run first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "subscription_plans" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('llm_usage_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('domain', sa.String(length=40), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('stage', sa.String(length=40), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('error_kind', sa.String(length=30), nullable=True),
        sa.Column('retries', sa.Integer(), nullable=False),
        sa.Column('retry_after_seconds', sa.Integer(), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('audit_id', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    op.create_index('ix_llm_usage_events_created_at', 'llm_usage_events', ['created_at'], unique=False)
    op.create_index('ix_llm_usage_events_domain', 'llm_usage_events', ['domain'], unique=False)
    op.create_index('ix_llm_usage_events_provider', 'llm_usage_events', ['provider'], unique=False)
    op.create_index('ix_llm_usage_events_username', 'llm_usage_events', ['username'], unique=False)
    op.create_table('payment_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('plan_code', sa.String(length=30), nullable=False),
        sa.Column('billing_cycle', sa.String(length=10), nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('gateway', sa.String(length=20), nullable=False),
        sa.Column('gateway_order_id', sa.String(length=120), nullable=False),
        sa.Column('gateway_payment_id', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('receipt_number', sa.String(length=40), nullable=True),
        sa.Column('failure_reason', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('gateway_order_id'),
        sa.UniqueConstraint('gateway_payment_id')
        )
    op.create_index('ix_payment_transactions_created_at', 'payment_transactions', ['created_at'], unique=False)
    op.create_index('ix_payment_transactions_username', 'payment_transactions', ['username'], unique=False)
    op.create_table('subscription_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('action', sa.String(length=30), nullable=False),
        sa.Column('actor', sa.String(length=80), nullable=False),
        sa.Column('from_plan', sa.String(length=30), nullable=True),
        sa.Column('to_plan', sa.String(length=30), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    op.create_index('ix_subscription_history_created_at', 'subscription_history', ['created_at'], unique=False)
    op.create_index('ix_subscription_history_username', 'subscription_history', ['username'], unique=False)
    op.create_table('subscription_plans',
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('tagline', sa.String(length=200), nullable=False),
        sa.Column('monthly_price_minor', sa.BigInteger(), nullable=True),
        sa.Column('yearly_price_minor', sa.BigInteger(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('monthly_credits', sa.Integer(), nullable=False),
        sa.Column('allowance_confirmed', sa.Boolean(), nullable=False),
        sa.Column('max_context_messages', sa.Integer(), nullable=False),
        sa.Column('allowed_providers', sa.String(length=200), nullable=True),
        sa.Column('fair_use_note', sa.Text(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('updated_by', sa.String(length=80), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('code')
        )
    op.create_table('usage_periods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('plan_code', sa.String(length=30), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('credits_allocated', sa.Integer(), nullable=False),
        sa.Column('credits_adjustment', sa.Integer(), nullable=False),
        sa.Column('credits_used', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username', 'period_start', name='uq_usage_period_user_start')
        )
    op.create_index('ix_usage_periods_username', 'usage_periods', ['username'], unique=False)
    op.create_table('plan_features',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_code', sa.String(length=30), nullable=False),
        sa.Column('feature', sa.String(length=40), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('credit_cost', sa.Integer(), nullable=False),
        sa.Column('period_limit', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['plan_code'], ['subscription_plans.code'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_code', 'feature', name='uq_plan_feature')
        )
    op.create_index('ix_plan_features_plan_code', 'plan_features', ['plan_code'], unique=False)
    op.create_table('usage_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('period_id', sa.Integer(), nullable=False),
        sa.Column('threshold', sa.Integer(), nullable=False),
        sa.Column('seen_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['period_id'], ['usage_periods.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('period_id', 'threshold', name='uq_usage_notification_period_threshold')
        )
    op.create_index('ix_usage_notifications_username', 'usage_notifications', ['username'], unique=False)
    op.create_table('usage_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=160), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('domain', sa.String(length=40), nullable=True),
        sa.Column('period_id', sa.Integer(), nullable=False),
        sa.Column('feature', sa.String(length=40), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('credits', sa.Integer(), nullable=False),
        sa.Column('audit_id', sa.String(length=40), nullable=True),
        sa.Column('actor', sa.String(length=80), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['period_id'], ['usage_periods.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key')
        )
    op.create_index('ix_usage_transactions_created_at', 'usage_transactions', ['created_at'], unique=False)
    op.create_index('ix_usage_transactions_period_id', 'usage_transactions', ['period_id'], unique=False)
    op.create_index('ix_usage_transactions_username', 'usage_transactions', ['username'], unique=False)
    op.create_table('user_subscriptions',
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('plan_code', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('billing_cycle', sa.String(length=10), nullable=False),
        sa.Column('current_period_start', sa.DateTime(), nullable=False),
        sa.Column('current_period_end', sa.DateTime(), nullable=False),
        sa.Column('auto_renew', sa.Boolean(), nullable=False),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False),
        sa.Column('scheduled_plan_code', sa.String(length=30), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['plan_code'], ['subscription_plans.code'], ),
        sa.PrimaryKeyConstraint('username')
        )
    op.create_index('ix_user_subscriptions_current_period_end', 'user_subscriptions', ['current_period_end'], unique=False)
    op.create_index('ix_user_subscriptions_plan_code', 'user_subscriptions', ['plan_code'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_subscriptions_plan_code', table_name='user_subscriptions')
    op.drop_index('ix_user_subscriptions_current_period_end', table_name='user_subscriptions')
    op.drop_table('user_subscriptions')
    op.drop_index('ix_usage_transactions_username', table_name='usage_transactions')
    op.drop_index('ix_usage_transactions_period_id', table_name='usage_transactions')
    op.drop_index('ix_usage_transactions_created_at', table_name='usage_transactions')
    op.drop_table('usage_transactions')
    op.drop_index('ix_usage_notifications_username', table_name='usage_notifications')
    op.drop_table('usage_notifications')
    op.drop_index('ix_plan_features_plan_code', table_name='plan_features')
    op.drop_table('plan_features')
    op.drop_index('ix_usage_periods_username', table_name='usage_periods')
    op.drop_table('usage_periods')
    op.drop_table('subscription_plans')
    op.drop_index('ix_subscription_history_username', table_name='subscription_history')
    op.drop_index('ix_subscription_history_created_at', table_name='subscription_history')
    op.drop_table('subscription_history')
    op.drop_index('ix_payment_transactions_username', table_name='payment_transactions')
    op.drop_index('ix_payment_transactions_created_at', table_name='payment_transactions')
    op.drop_table('payment_transactions')
    op.drop_index('ix_llm_usage_events_username', table_name='llm_usage_events')
    op.drop_index('ix_llm_usage_events_provider', table_name='llm_usage_events')
    op.drop_index('ix_llm_usage_events_domain', table_name='llm_usage_events')
    op.drop_index('ix_llm_usage_events_created_at', table_name='llm_usage_events')
    op.drop_table('llm_usage_events')

from alembic import op

revision = "0041"
down_revision = "0040"


def upgrade() -> None:
    op.execute("ALTER TYPE payment_gateway_type ADD VALUE IF NOT EXISTS 'ANORE'")
    op.execute("CREATE TABLE anore_webhook_deliveries (delivery_id TEXT PRIMARY KEY)")


def downgrade() -> None:
    op.drop_table("anore_webhook_deliveries")

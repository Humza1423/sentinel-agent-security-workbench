"""Keep execution mode stable across conversation restoration."""

import sqlalchemy as sa
from alembic import op

revision = "0007_live_sessions"
down_revision = "0006_usage_budget"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chat_sessions",
        sa.Column("mode", sa.String(24), nullable=False, server_default="fixture_execution"),
    )


def downgrade():
    op.drop_column("chat_sessions", "mode")

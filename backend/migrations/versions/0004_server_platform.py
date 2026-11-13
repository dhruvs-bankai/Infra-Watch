"""Track the monitored server platform (Linux or Windows)."""

from alembic import op
import sqlalchemy as sa


revision = "0004_server_platform"
down_revision = "0003_monitoring_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("platform", sa.String(length=10), nullable=False, server_default="linux"),
    )


def downgrade() -> None:
    op.drop_column("servers", "platform")

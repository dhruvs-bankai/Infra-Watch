"""Add monitoring integrations and external endpoint monitors."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid


revision = "0003_monitoring_integrations"
down_revision = "0002_alert_event_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "server_integrations",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("server_id", Uuid(as_uuid=True), sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("metrics_path", sa.String(length=200), nullable=False, server_default="/metrics"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("status_message", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("server_id", "kind", name="uq_server_integration_kind"),
    )
    op.create_index("ix_server_integrations_server_id", "server_integrations", ["server_id"])

    op.create_table(
        "endpoint_monitors",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("target", sa.String(length=2048), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(length=200), nullable=False, server_default="/"),
        sa.Column("expected_status", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("environment", sa.String(length=20), nullable=False, server_default="production"),
        sa.Column("description", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("server_id", Uuid(as_uuid=True), sa.ForeignKey("servers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "target", "port", name="uq_endpoint_monitor_target"),
    )
    op.create_index("ix_endpoint_monitors_name", "endpoint_monitors", ["name"], unique=True)
    op.create_index("ix_endpoint_monitors_kind", "endpoint_monitors", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_endpoint_monitors_kind", table_name="endpoint_monitors")
    op.drop_index("ix_endpoint_monitors_name", table_name="endpoint_monitors")
    op.drop_table("endpoint_monitors")
    op.drop_index("ix_server_integrations_server_id", table_name="server_integrations")
    op.drop_table("server_integrations")

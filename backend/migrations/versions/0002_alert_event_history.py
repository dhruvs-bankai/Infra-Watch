"""Persist active and resolved Alertmanager notifications."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid


revision = "0002_alert_event_history"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_events",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="info"),
        sa.Column("instance", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("summary", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("description", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", "started_at", name="uq_alert_event_firing"),
    )
    op.create_index("ix_alert_events_fingerprint", "alert_events", ["fingerprint"])
    op.create_index("ix_alert_events_status", "alert_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_alert_events_status", table_name="alert_events")
    op.drop_index("ix_alert_events_fingerprint", table_name="alert_events")
    op.drop_table("alert_events")

"""add transactional outbox for Agent query read sets

Revision ID: e1d5a0c9b347
Revises: d4e2a91c6b37
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1d5a0c9b347"
down_revision: str | Sequence[str] | None = "d4e2a91c6b37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_readset_outbox",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_agent_readset_outbox_run_ordinal"),
    )
    op.create_index(
        "ix_agent_readset_outbox_run_id", "agent_readset_outbox", ["run_id"], unique=False
    )
    op.create_index(
        "ix_agent_readset_outbox_pending",
        "agent_readset_outbox",
        ["delivered_at", "created_at"],
        unique=False,
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_agent_readset_outbox_payload_immutable "
            "BEFORE UPDATE OF event_id, run_id, ordinal, payload, payload_sha256, created_at "
            "ON agent_readset_outbox BEGIN "
            "SELECT RAISE(ABORT, 'immutable Agent read-set outbox payload'); END"
        )


def downgrade() -> None:
    connection = op.get_bind()
    pending = connection.execute(
        sa.text("SELECT 1 FROM agent_readset_outbox WHERE delivered_at IS NULL LIMIT 1")
    ).first()
    if pending:
        raise RuntimeError("Cannot drop the Agent read-set outbox while undelivered events remain.")
    if connection.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_agent_readset_outbox_payload_immutable")
    op.drop_index("ix_agent_readset_outbox_pending", table_name="agent_readset_outbox")
    op.drop_index("ix_agent_readset_outbox_run_id", table_name="agent_readset_outbox")
    op.drop_table("agent_readset_outbox")

"""add transactional outbox for indexed Agent steps

Revision ID: e2c6b8d0a194
Revises: e1d5a0c9b347
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2c6b8d0a194"
down_revision: str | Sequence[str] | None = "e1d5a0c9b347"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_step_outbox",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("step_id", name="uq_agent_step_outbox_step_id"),
    )
    op.create_index("ix_agent_step_outbox_step_id", "agent_step_outbox", ["step_id"])
    op.create_index("ix_agent_step_outbox_run_id", "agent_step_outbox", ["run_id"])
    op.create_index(
        "ix_agent_step_outbox_pending",
        "agent_step_outbox",
        ["delivered_at", "created_at"],
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_agent_step_outbox_payload_immutable "
            "BEFORE UPDATE OF event_id, step_id, run_id, kind, payload_sha256, created_at "
            "ON agent_step_outbox BEGIN "
            "SELECT RAISE(ABORT, 'immutable Agent step outbox payload'); END"
        )


def downgrade() -> None:
    connection = op.get_bind()
    pending = connection.execute(
        sa.text("SELECT 1 FROM agent_step_outbox WHERE delivered_at IS NULL LIMIT 1")
    ).first()
    if pending:
        raise RuntimeError("Cannot drop the Agent step outbox while undelivered events remain.")
    if connection.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_agent_step_outbox_payload_immutable")
    op.drop_index("ix_agent_step_outbox_pending", table_name="agent_step_outbox")
    op.drop_index("ix_agent_step_outbox_run_id", table_name="agent_step_outbox")
    op.drop_index("ix_agent_step_outbox_step_id", table_name="agent_step_outbox")
    op.drop_table("agent_step_outbox")

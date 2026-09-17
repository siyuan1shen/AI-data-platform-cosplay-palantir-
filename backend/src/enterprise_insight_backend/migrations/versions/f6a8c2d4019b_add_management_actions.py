"""add project-scoped management actions and immutable event history

Revision ID: f6a8c2d4019b
Revises: c38d9e1a2b74
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a8c2d4019b"
down_revision: str | Sequence[str] | None = "c38d9e1a2b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "management_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("priority", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_done_by", sa.String(length=128), nullable=True),
        sa.Column("verified_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_done_by", sa.String(length=128), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'CANCELLED')",
            name="ck_management_action_status",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
            name="ck_management_action_priority",
        ),
        sa.CheckConstraint(
            "verified_done_at IS NULL OR reported_done_at IS NOT NULL",
            name="ck_management_action_verified_requires_reported",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_management_actions_company_id", "management_actions", ["company_id"])
    op.create_index("ix_management_actions_project_id", "management_actions", ["project_id"])
    op.create_index("ix_management_actions_priority", "management_actions", ["priority"])
    op.create_index("ix_management_actions_status", "management_actions", ["status"])
    op.create_index(
        "ix_management_action_project_status_due",
        "management_actions",
        ["project_id", "status", "due_at"],
    )

    op.create_table(
        "management_action_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["action_id"], ["management_actions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", "revision", name="uq_management_action_event_revision"),
    )
    op.create_index(
        "ix_management_action_events_company_id", "management_action_events", ["company_id"]
    )
    op.create_index(
        "ix_management_action_events_project_id", "management_action_events", ["project_id"]
    )
    op.create_index(
        "ix_management_action_events_action_id", "management_action_events", ["action_id"]
    )
    op.create_index(
        "ix_management_action_events_event_type", "management_action_events", ["event_type"]
    )
    op.create_index(
        "ix_management_action_event_project_created",
        "management_action_events",
        ["project_id", "created_at"],
    )
    _create_event_immutability_guards()


def downgrade() -> None:
    _drop_event_immutability_guards()
    op.drop_table("management_action_events")
    op.drop_table("management_actions")


def _create_event_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER management_action_events_no_{operation.lower()}
                BEFORE {operation} ON management_action_events
                BEGIN
                    SELECT RAISE(ABORT, 'management action events are append-only');
                END;
                """
            )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_management_action_event_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'management action events are append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER management_action_events_immutable
            BEFORE UPDATE OR DELETE ON management_action_events
            FOR EACH ROW EXECUTE FUNCTION reject_management_action_event_mutation();
            """
        )


def _drop_event_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS management_action_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS management_action_events_no_delete")
    elif dialect == "postgresql":
        drop_trigger = "DROP TRIGGER IF EXISTS management_action_events_immutable"
        op.execute(f"{drop_trigger} ON management_action_events")
        op.execute("DROP FUNCTION IF EXISTS reject_management_action_event_mutation()")

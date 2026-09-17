"""add company-level canonical projection pointers

Revision ID: e3f1a2b4c5d6
Revises: e2c6b8d0a194
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f1a2b4c5d6"
down_revision: str | Sequence[str] | None = "e2c6b8d0a194"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("canonical_project_id", sa.String(length=36)))
    op.add_column(
        "projects",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("projects", sa.Column("canonical_project_id", sa.String(length=36)))
    op.create_index("ix_companies_canonical_project_id", "companies", ["canonical_project_id"])
    op.create_index("ix_projects_is_primary", "projects", ["is_primary"])
    op.create_index("ix_projects_canonical_project_id", "projects", ["canonical_project_id"])

    # Existing databases keep their data.  The oldest project becomes the
    # compatibility storage key for its company; other projects remain
    # addressable historical work items until the explicit merge migration.
    bind = op.get_bind()
    # Pick exactly one deterministic compatibility project per company.  The
    # old databases can contain rows with identical timestamps, so a
    # MIN(created_at) join would incorrectly mark several projects primary.
    bind.execute(
        sa.text(
            "UPDATE projects SET is_primary = 1 "
            "WHERE id = (SELECT first_project.id FROM projects first_project "
            "WHERE first_project.company_id = projects.company_id "
            "ORDER BY first_project.created_at, first_project.id LIMIT 1)"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE companies SET canonical_project_id = "
            "(SELECT p.id FROM projects p WHERE p.company_id = companies.id "
            "AND p.is_primary = 1 ORDER BY p.created_at, p.id LIMIT 1)"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE projects SET canonical_project_id = "
            "(SELECT c.canonical_project_id FROM companies c "
            "WHERE c.id = projects.company_id)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_projects_canonical_project_id", table_name="projects")
    op.drop_index("ix_projects_is_primary", table_name="projects")
    op.drop_index("ix_companies_canonical_project_id", table_name="companies")
    op.drop_column("projects", "canonical_project_id")
    op.drop_column("projects", "is_primary")
    op.drop_column("companies", "canonical_project_id")

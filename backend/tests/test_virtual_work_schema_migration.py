from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import inspect, select

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationDatabase,
)


def test_observation_schema_v3_upgrades_additively_to_virtual_work_v4(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        observation_database_url=f"sqlite:///{(tmp_path / 'legacy-observations.db').as_posix()}",
    )
    database = ObservationDatabase(settings)
    observation_id = str(uuid4())

    # Recreate the minimum existing v3 layout without any virtual-work tables.
    ManagementObservationRow.__table__.create(database.engine)
    with database.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=3")
    now = datetime.now(UTC)
    with database.session_factory.begin() as session:
        session.add(
            ManagementObservationRow(
                id=observation_id,
                company_id=str(uuid4()),
                project_id=str(uuid4()),
                kind="MEETING",
                title="保留的既有记录",
                content="v3 数据不得在升级时丢失。",
                content_sha256="a" * 64,
                occurred_at=None,
                submitted_by="test-owner",
                status="ACTIVE",
                revision=1,
                idempotency_key=None,
                created_at=now,
                updated_at=now,
            )
        )

    database.create_schema()

    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 4
        tables = set(inspect(connection).get_table_names())
    assert {
        "management_observations",
        "virtual_work_models",
        "virtual_work_revisions",
        "virtual_work_nodes",
        "virtual_work_edges",
        "virtual_work_evidence",
        "virtual_work_assertions",
        "virtual_work_assertion_evidence",
        "virtual_work_real_anchors",
        "virtual_work_reviews",
    }.issubset(tables)

    with database.session_factory() as session:
        preserved = session.scalar(
            select(ManagementObservationRow).where(
                ManagementObservationRow.id == observation_id
            )
        )
    assert preserved is not None
    assert preserved.content == "v3 数据不得在升级时丢失。"
    database.engine.dispose()

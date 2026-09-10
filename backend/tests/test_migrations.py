from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Integer, MetaData, inspect, select, text

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.migration import DATABASE_SCHEMA_REVISION, _config
from enterprise_insight_backend.models import Base, CompanyRow, ProjectRow


@pytest.mark.parametrize(
    "revision",
    [item.revision for item in ScriptDirectory.from_config(_config("sqlite://")).walk_revisions()],
)
def test_every_historical_revision_upgrades_and_preserves_company(
    tmp_path: Path, revision: str
) -> None:
    database = _database(tmp_path, f"历史版本-{revision}.db")
    config = _config(str(database.engine.url))
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
    with database.session_factory.begin() as session:
        session.add(CompanyRow(name=f"升级前企业-{revision}"))
    database.create_schema()
    backups = list((tmp_path / "schema-backups").glob("*.db"))
    assert len(backups) == (0 if revision == DATABASE_SCHEMA_REVISION else 1)
    database.create_schema()
    assert list((tmp_path / "schema-backups").glob("*.db")) == backups
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            DATABASE_SCHEMA_REVISION
        )
        assert connection.scalar(text("SELECT name FROM companies")) == f"升级前企业-{revision}"
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    actual = inspect(database.engine)
    for name, table in Base.metadata.tables.items():
        assert actual.has_table(name), (revision, name)
        assert set(table.columns.keys()) <= {column["name"] for column in actual.get_columns(name)}
    database.engine.dispose()


def test_unversioned_database_with_missing_index_is_not_adopted(tmp_path: Path) -> None:
    database = _database(tmp_path, "missing-index.db")
    Base.metadata.create_all(database.engine)
    index = next(iter(Base.metadata.tables["companies"].indexes))
    with database.engine.begin() as connection:
        index.drop(connection)
    with pytest.raises(RuntimeError, match="incomplete unversioned database"):
        database.create_schema()
    assert not inspect(database.engine).has_table("alembic_version")
    database.engine.dispose()


def test_failed_upgrade_retains_a_readable_pre_upgrade_backup(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    database = _database(tmp_path, "upgrade-failure.db")
    config = _config(str(database.engine.url))
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "74cf2e16a0c8")
    with database.session_factory.begin() as session:
        session.add(CompanyRow(name="升级失败仍可恢复"))

    def fail_upgrade(config, revision):
        connection = config.attributes["connection"]
        connection.exec_driver_sql("ALTER TABLE companies ADD COLUMN partial_change INTEGER")
        connection.exec_driver_sql("DELETE FROM companies")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(command, "upgrade", fail_upgrade)
    with pytest.raises(RuntimeError, match="Pre-upgrade SQLite backup retained"):
        database.create_schema()
    backups = list((tmp_path / "schema-backups").glob("*.db"))
    assert len(backups) == 1
    assert "partial_change" not in {
        column["name"] for column in inspect(database.engine).get_columns("companies")
    }
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT name FROM companies")) == "升级失败仍可恢复"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT name FROM companies").fetchone()[0] == (
            "升级失败仍可恢复"
        )
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "74cf2e16a0c8"
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    database.engine.dispose()


def test_metric_upgrade_preserves_child_observations_and_versions(tmp_path: Path) -> None:
    database = _database(tmp_path, "legacy-metric.db")
    config = _config(str(database.engine.url))
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "8c4e1a6d9b20")
    with database.session_factory.begin() as session:
        company = CompanyRow(name="指标迁移企业")
        session.add(company)
        session.flush()
        project = ProjectRow(company_id=company.id, name="历史经营指标")
        session.add(project)
        session.flush()
        project_id = project.id
    metadata = MetaData()
    metadata.reflect(database.engine)
    timestamp = datetime.now(UTC)
    with database.engine.begin() as connection:
        connection.execute(metadata.tables["metric_definitions"].insert().values(
            id="metric-old", project_id=project_id, key="delivery", name="交付率",
            scope="LOCAL", direction="HIGHER_IS_BETTER", unit="%", target_value=95,
            properties={}, active=True, revision=1, created_at=timestamp, updated_at=timestamp,
        ))
        for index, value in enumerate([80, 85]):
            connection.execute(metadata.tables["metric_observations"].insert().values(
                id=f"observation-{index}", project_id=project_id,
                metric_definition_id="metric-old", period_key="2026-08", observed_at=timestamp,
                value=value, numeric_value=value, status="MISS", source="manual", evidence=[],
                created_at=timestamp,
            ))
    database.create_schema()
    with database.engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, value, version, record_status, supersedes_id "
            "FROM metric_observations ORDER BY version"
        )).mappings().all()
        assert len(rows) == 2
        assert [row["version"] for row in rows] == [1, 2]
        assert [row["record_status"] for row in rows] == ["SUPERSEDED", "ACTIVE"]
        assert rows[1]["supersedes_id"] == rows[0]["id"]
        assert connection.scalar(text("SELECT entity_id FROM metric_definitions"))
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    database.engine.dispose()


@pytest.mark.parametrize("damage", ["type", "nullable", "foreign_key", "unique"])
def test_unversioned_schema_constraints_are_verified(tmp_path: Path, damage: str) -> None:
    database = _database(tmp_path, f"incorrect-{damage}.db")
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    if damage == "type":
        metadata.tables["companies"].c.name.type = Integer()
    elif damage == "nullable":
        metadata.tables["companies"].c.name.nullable = True
    elif damage == "foreign_key":
        projects = metadata.tables["projects"]
        projects.constraints.remove(next(iter(projects.foreign_key_constraints)))
    else:
        from sqlalchemy import UniqueConstraint

        table, constraint = next(
            (table, constraint)
            for table in metadata.tables.values()
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        )
        table.constraints.remove(constraint)
    metadata.create_all(database.engine)
    with pytest.raises(RuntimeError, match="incomplete unversioned database"):
        database.create_schema()
    assert not inspect(database.engine).has_table("alembic_version")
    database.engine.dispose()


def _database(tmp_path: Path, name: str) -> Database:
    path = tmp_path / name
    return Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{path.as_posix()}",
            agent_worker_enabled=False,
        )
    )


def test_new_database_is_created_by_versioned_migration(tmp_path: Path) -> None:
    database = _database(tmp_path, "new.db")
    database.create_schema()
    database.create_schema()

    tables = set(inspect(database.engine).get_table_names())
    assert "alembic_version" in tables
    assert {"companies", "entities", "relations", "agent_runs", "action_invocations"}.issubset(
        tables
    )
    assert {
        "metric_definitions",
        "meeting_records",
        "management_signals",
        "management_insights",
        "information_requests",
        "evaluation_suites",
        "evaluation_cases",
        "evaluation_runs",
        "evaluation_results",
        "evaluation_executions",
        "query_snapshots",
        "observation_conflicts",
        "source_assets",
        "raw_batches",
        "raw_records",
        "materialization_runs",
        "semantic_datasets",
        "semantic_query_runs",
        "management_issues",
        "management_issue_feedback",
        "restore_previews",
    }.issubset(tables)
    database.engine.dispose()


def test_legacy_unversioned_database_is_adopted_without_data_loss(tmp_path: Path) -> None:
    database = _database(tmp_path, "legacy.db")
    Base.metadata.create_all(database.engine)
    with database.session_factory.begin() as session:
        session.add(CompanyRow(name="保留企业"))

    database.create_schema()

    with database.session_factory() as session:
        assert session.scalar(select(CompanyRow.name)) == "保留企业"
    assert inspect(database.engine).has_table("alembic_version")
    database.engine.dispose()


def test_incomplete_unversioned_database_is_not_stamped_as_current(tmp_path: Path) -> None:
    database = _database(tmp_path, "incomplete-legacy.db")
    with database.engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE companies (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200))")
        )
        connection.execute(text("INSERT INTO companies (id, name) VALUES ('legacy-1', '旧企业')"))

    with pytest.raises(RuntimeError, match="incomplete unversioned database"):
        database.create_schema()

    inspector = inspect(database.engine)
    assert not inspector.has_table("alembic_version")
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT name FROM companies")) == "旧企业"
    database.engine.dispose()

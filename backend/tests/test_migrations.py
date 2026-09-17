from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Integer, MetaData, inspect, select, text

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.migration import DATABASE_SCHEMA_REVISION, _config
from enterprise_insight_backend.models import Base, CompanyRow


def _legacy_company(database: Database, name: str) -> str:
    """Insert a company using only columns present in pre-scope revisions."""
    company_id = str(uuid4())
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, name, industry, description, created_at, updated_at) "
                "VALUES (:id, :name, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": company_id, "name": name},
        )
    return company_id


def _legacy_project(database: Database, company_id: str, name: str) -> str:
    """Insert a project using only columns present before projection scoping."""
    project_id = str(uuid4())
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id, company_id, name, description, status, revision, created_at, updated_at) "
                "VALUES (:id, :company_id, :name, NULL, 'ACTIVE', 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": project_id, "company_id": company_id, "name": name},
        )
    return project_id


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
    # CompanyRow represents the current schema and may contain fields added by
    # later migrations. Insert only columns guaranteed by this historical
    # revision, then let create_schema upgrade the database.
    _legacy_company(database, f"升级前企业-{revision}")
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


def test_management_action_upgrade_adds_idempotency_without_losing_history(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "management-action-idempotency-upgrade.db")
    config = _config(str(database.engine.url))
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "f6a8c2d4019b")
    company_id = _legacy_company(database, "管理行动升级保留企业")
    project_id = _legacy_project(database, company_id, "保留行动历史")

    timestamp = datetime.now(UTC).isoformat()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO management_actions (
                    id, company_id, project_id, title, description, owner,
                    priority, status, due_at, reported_done_at, reported_done_by,
                    verified_done_at, verified_done_by, revision, created_by,
                    created_at, updated_at
                ) VALUES (
                    'action-before-upgrade', :company_id, :project_id,
                    '升级前的现实行动', NULL, '负责人', 'HIGH', 'IN_PROGRESS',
                    NULL, NULL, NULL, NULL, NULL, 2, 'local-owner', :timestamp, :timestamp
                )
                """
            ),
            {"company_id": company_id, "project_id": project_id, "timestamp": timestamp},
        )
        connection.execute(
            text(
                """
                INSERT INTO management_action_events (
                    id, company_id, project_id, action_id, revision, event_type,
                    message, details, from_status, to_status, actor_id, reason, created_at
                ) VALUES (
                    'event-before-upgrade', :company_id, :project_id,
                    'action-before-upgrade', 2, 'PROGRESS', '升级前进展', '{}',
                    'OPEN', 'IN_PROGRESS', 'local-owner', '保留历史', :timestamp
                )
                """
            ),
            {"company_id": company_id, "project_id": project_id, "timestamp": timestamp},
        )

    database.create_schema()

    with database.engine.connect() as connection:
        action = connection.execute(
            text(
                "SELECT title, revision, idempotency_key, idempotency_hash "
                "FROM management_actions WHERE id='action-before-upgrade'"
            )
        ).one()
        event_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM management_action_events "
                "WHERE action_id='action-before-upgrade'"
            )
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    assert tuple(action) == ("升级前的现实行动", 2, None, None)
    assert event_count == 1
    index_names = {
        item["name"] for item in inspect(database.engine).get_indexes("management_actions")
    }
    assert "uq_management_action_project_idempotency" in index_names
    database.engine.dispose()


def test_source_secret_migration_adds_ciphertext_column_without_needing_a_key(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "source-secret-upgrade.db")
    config = _config(str(database.engine.url))
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "b7296f0c31ad")
    company_id = _legacy_company(database, "数据源凭据迁移")
    project_id = _legacy_project(database, company_id, "旧连接配置")

    legacy_profile = {
        "base_url": "https://erp.invalid",
        "api_key": "legacy-key-never-plaintext-after-first-use",
        "nested": {"password": "legacy-password"},
        "timeout": 20,
    }
    timestamp = datetime.now(UTC).isoformat()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_systems ("
                "id, project_id, name, kind, description, connection_profile, status, "
                "last_tested_at, revision, created_at, updated_at"
                ") VALUES ("
                "'source-legacy', :project_id, '旧ERP', 'ERP', NULL, :profile, "
                "'CONFIGURED', NULL, 1, :timestamp, :timestamp)"
            ),
            {
                "project_id": project_id,
                "profile": json.dumps(legacy_profile),
                "timestamp": timestamp,
            },
        )

    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        stored_profile, encrypted = connection.execute(
            text(
                "SELECT connection_profile, encrypted_connection_secrets "
                "FROM source_systems WHERE id='source-legacy'"
            )
        ).one()
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))

    assert revision == DATABASE_SCHEMA_REVISION
    assert json.loads(stored_profile) == legacy_profile
    assert encrypted is None
    assert not (tmp_path / "model-profile.key").exists()
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
    _legacy_company(database, "升级失败仍可恢复")

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
    company_id = _legacy_company(database, "指标迁移企业")
    project_id = _legacy_project(database, company_id, "历史经营指标")
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

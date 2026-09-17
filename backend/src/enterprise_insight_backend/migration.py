from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, UniqueConstraint, inspect
from sqlalchemy.engine.reflection import Inspector

from enterprise_insight_backend.models import Base

DATABASE_SCHEMA_REVISION = "c3d4e5f6a7b8"


def upgrade_database(engine: Engine, database_url: str) -> None:
    """Upgrade a database without pretending an incomplete schema is current."""
    config = _config(database_url)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    application_tables = table_names.intersection(Base.metadata.tables)
    has_version_table = inspector.has_table("alembic_version")

    with engine.connect() as connection:
        revisions = MigrationContext.configure(connection).get_current_heads()
    if revisions == (DATABASE_SCHEMA_REVISION,):
        return

    if application_tables and not has_version_table:
        missing = _missing_schema_members(inspector)
        if missing:
            details = ", ".join(missing[:8])
            if len(missing) > 8:
                details += f", and {len(missing) - 8} more"
            raise RuntimeError(
                "Refusing to stamp an incomplete unversioned database as current. "
                f"Missing schema members: {details}. Back up the database and run "
                "a supported legacy import or migration first."
            )
        # Early v3 development builds created the complete current SQLAlchemy schema
        # before Alembic existed. Only an exact-enough current schema may be adopted.
        _backup_sqlite_before_upgrade(engine)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.stamp(config, "head")
        return

    backup = _backup_sqlite_before_upgrade(engine) if application_tables else None
    try:
        _run_upgrade(engine, config)
    except Exception as exc:
        if backup is not None:
            raise RuntimeError(
                f"Database upgrade failed. Pre-upgrade SQLite backup retained at: {backup}"
            ) from exc
        raise


def _run_upgrade(engine: Engine, config: Config) -> None:
    if engine.dialect.name != "sqlite":
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        return
    # Alembic batch mode drops/recreates parent tables. SQLite ON DELETE CASCADE
    # would otherwise delete child records during that temporary drop. Only this
    # migration connection suspends enforcement; validate before atomic commit.
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                # Explicit BEGIN also makes SQLite DDL transactional in legacy
                # sqlite3 transaction mode, and excludes concurrent writers.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    raise RuntimeError("Migration produced invalid foreign key references")
        finally:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _backup_sqlite_before_upgrade(engine: Engine) -> Path | None:
    """Keep a consistent copy, including committed WAL data, before schema writes."""
    if engine.dialect.name != "sqlite" or not engine.url.database:
        return None
    database_path = Path(engine.url.database)
    if not database_path.is_file():
        return None
    directory = database_path.resolve().parent / "schema-backups"
    directory.mkdir(exist_ok=True)
    with NamedTemporaryFile(
        prefix=f"{database_path.stem}-before-upgrade-", suffix=".db", dir=directory, delete=False
    ) as temporary:
        backup = Path(temporary.name)
    try:
        with engine.connect() as source:
            with closing(sqlite3.connect(backup)) as destination:
                driver_connection = cast(Any, source.connection.driver_connection)
                driver_connection.backup(destination)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _missing_schema_members(inspector: Inspector) -> list[str]:
    missing: list[str] = []
    existing_tables = set(inspector.get_table_names())
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            missing.append(f"table:{table_name}")
            continue
        existing_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        missing.extend(
            f"column:{table_name}.{column.name}"
            for column in table.columns
            if column.name not in existing_columns
        )
        for column in table.columns:
            actual = existing_columns.get(column.name)
            if actual is None:
                continue
            expected_type = column.type.compile(dialect=inspector.bind.dialect)
            actual_type = actual["type"].compile(dialect=inspector.bind.dialect)
            if expected_type != actual_type:
                missing.append(f"type:{table_name}.{column.name}")
            if actual["nullable"] != column.nullable:
                missing.append(f"nullable:{table_name}.{column.name}")
        primary_key = inspector.get_pk_constraint(table_name).get("constrained_columns", [])
        if list(table.primary_key.columns.keys()) != primary_key:
            missing.append(f"primary_key:{table_name}")
        unique_keys = {
            tuple(item["column_names"]) for item in inspector.get_unique_constraints(table_name)
        }
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                if tuple(constraint.columns.keys()) not in unique_keys:
                    missing.append(f"unique:{table_name}.{constraint.name}")
        indexes = {
            (tuple(item["column_names"]), bool(item["unique"]))
            for item in inspector.get_indexes(table_name)
        }
        for index in table.indexes:
            if (tuple(index.columns.keys()), bool(index.unique)) not in indexes:
                missing.append(f"index:{table_name}.{index.name}")
        foreign_keys = {
            (
                tuple(item["constrained_columns"]), item["referred_table"],
                tuple(item["referred_columns"]), item.get("options", {}).get("ondelete"),
            )
            for item in inspector.get_foreign_keys(table_name)
        }
        for constraint in table.foreign_key_constraints:
            expected = (
                tuple(constraint.columns.keys()), constraint.referred_table.name,
                tuple(item.column.name for item in constraint.elements), constraint.ondelete,
            )
            if expected not in foreign_keys:
                missing.append(f"foreign_key:{table_name}.{constraint.name}")
    return missing


def _config(database_url: str) -> Config:
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config

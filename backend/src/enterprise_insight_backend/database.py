from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.migration import upgrade_database


class Database:
    def __init__(self, settings: Settings) -> None:
        connect_args = (
            {"check_same_thread": False}
            if settings.resolved_database_url.startswith("sqlite")
            else {}
        )
        self.engine: Engine = create_engine(
            settings.resolved_database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        if settings.resolved_database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def create_schema(self) -> None:
        upgrade_database(self.engine, str(self.engine.url))

    def session_dependency(self) -> Generator[Session, None, None]:
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise


def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

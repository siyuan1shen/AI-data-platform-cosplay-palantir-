from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Generator[Session, None, None]:
    yield from request.app.state.database.session_dependency()


def get_observation_session(request: Request) -> Generator[Session, None, None]:
    yield from request.app.state.observation_database.session_dependency()


def get_control_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.control_database.session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

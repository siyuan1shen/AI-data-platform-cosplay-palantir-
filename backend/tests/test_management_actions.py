from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, update
from sqlalchemy.exc import IntegrityError

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.management_actions import ManagementActionService
from enterprise_insight_backend.models import ManagementActionEventRow
from enterprise_insight_backend.schemas import (
    ManagementActionCreate,
    ManagementActionEventCreate,
    ManagementActionRevisionRequest,
    ManagementActionUpdate,
    ManagementActionVerifyDone,
)


def _project(client: TestClient, suffix: str) -> str:
    company = client.post("/api/v3/companies", json={"name": f"管理行动测试公司-{suffix}"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects",
        json={"name": f"管理行动测试项目-{suffix}"},
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def _call(client: TestClient, operation):
    with client.app.state.database.session_factory() as session:
        try:
            result = operation(ManagementActionService(session))
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def _create_action(client: TestClient, project_id: str = ""):
    selected_project = project_id or _project(client, "action")
    action = _call(
        client,
        lambda service: service.create(
            UUID(selected_project),
            ManagementActionCreate(
                title="完成客户交付流程复盘", description="厘清延期原因", reason="季度复盘"
            ),
            actor_id="负责人-甲",
        ),
    )
    return selected_project, action


def test_create_list_and_get_are_project_scoped(client: TestClient) -> None:
    project_id, action = _create_action(client)
    other_project_id = _project(client, "other")

    assert action.status == "OPEN"
    assert action.revision == 1
    assert action.reported_done is False
    assert action.verified_done is False

    own_list = _call(
        client, lambda service: service.list(UUID(project_id), include_cancelled=False)
    )
    other_list = _call(
        client,
        lambda service: service.list(UUID(other_project_id), include_cancelled=False),
    )
    assert own_list.total == 1
    assert own_list.items[0].id == action.id
    assert other_list.total == 0

    with pytest.raises(DomainError) as not_found:
        _call(
            client,
            lambda service: service.get(UUID(other_project_id), action.id),
        )
    assert not_found.value.status_code == 404

    history = _call(client, lambda service: service.history(UUID(project_id), action.id))
    assert history.total == 1
    assert history.items[0].event_type == "CREATED"
    assert history.items[0].actor_id == "负责人-甲"
    assert history.items[0].reason == "季度复盘"

    foreign_keys = inspect(client.app.state.database.engine).get_foreign_keys("management_actions")
    formal_references = {
        (foreign_key["constrained_columns"][0], foreign_key["referred_table"])
        for foreign_key in foreign_keys
    }
    assert ("company_id", "companies") in formal_references
    assert ("project_id", "projects") in formal_references


def test_updates_events_and_done_states_are_revisioned_and_distinct(
    client: TestClient,
) -> None:
    project_id, action = _create_action(client)
    project_uuid = UUID(project_id)

    updated = _call(
        client,
        lambda service: service.update(
            project_uuid,
            action.id,
            ManagementActionUpdate(
                status="IN_PROGRESS", owner="交付负责人", expected_revision=1, reason="开始执行"
            ),
            actor_id="负责人-乙",
        ),
    )
    assert updated.status == "IN_PROGRESS"
    assert updated.revision == 2

    with pytest.raises(DomainError) as conflict:
        _call(
            client,
            lambda service: service.update(
                project_uuid,
                action.id,
                ManagementActionUpdate(title="过期写入", expected_revision=1),
                actor_id="负责人-乙",
            ),
        )
    assert conflict.value.status_code == 409

    progress = _call(
        client,
        lambda service: service.append_progress(
            project_uuid,
            action.id,
            ManagementActionEventCreate(
                message="已完成访谈", expected_revision=2, details={"访谈数": 4}
            ),
            actor_id="负责人-丙",
        ),
    )
    outcome = _call(
        client,
        lambda service: service.append_outcome(
            project_uuid,
            action.id,
            ManagementActionEventCreate(
                message="交付延期主要发生在需求变更确认环节", expected_revision=3
            ),
            actor_id="负责人-丙",
        ),
    )
    assert (progress.revision, progress.event_type) == (3, "PROGRESS")
    assert (outcome.revision, outcome.event_type) == (4, "OUTCOME")

    reported = _call(
        client,
        lambda service: service.report_done(
            project_uuid,
            action.id,
            ManagementActionEventCreate(
                message="复盘报告已提交", expected_revision=4, reason="执行方自报"
            ),
            actor_id="执行负责人",
        ),
    )
    assert reported.reported_done is True
    assert reported.verified_done is False
    assert reported.reported_done_by == "执行负责人"
    assert reported.revision == 5

    verified = _call(
        client,
        lambda service: service.verify_done(
            project_uuid,
            action.id,
            ManagementActionVerifyDone(
                expected_revision=5, verification_note="抽查访谈记录与报告一致", reason="管理复核"
            ),
            actor_id="复核人",
        ),
    )
    assert verified.reported_done is True
    assert verified.verified_done is True
    assert verified.verified_done_by == "复核人"
    assert verified.revision == 6

    history = _call(client, lambda service: service.history(project_uuid, action.id))
    assert [event.revision for event in history.items] == [1, 2, 3, 4, 5, 6]
    assert [event.event_type for event in history.items] == [
        "CREATED",
        "UPDATED",
        "PROGRESS",
        "OUTCOME",
        "DONE_REPORTED",
        "DONE_VERIFIED",
    ]
    assert history.items[-1].actor_id == "复核人"
    assert history.items[-1].reason == "管理复核"
    assert history.items[-1].message == "抽查访谈记录与报告一致"
    first_page = _call(
        client,
        lambda service: service.history(project_uuid, action.id, limit=2),
    )
    assert first_page.total == 6
    assert [event.revision for event in first_page.items] == [1, 2]


def test_done_cannot_be_verified_before_report_and_cancel_is_revisioned(
    client: TestClient,
) -> None:
    project_id, action = _create_action(client)
    project_uuid = UUID(project_id)

    with pytest.raises(DomainError) as not_reported:
        _call(
            client,
            lambda service: service.verify_done(
                project_uuid,
                action.id,
                ManagementActionVerifyDone(expected_revision=1, verification_note="已核实"),
                actor_id="复核人",
            ),
        )
    assert not_reported.value.code == "MANAGEMENT_ACTION_NOT_REPORTED_DONE"

    cancelled = _call(
        client,
        lambda service: service.cancel(
            project_uuid,
            action.id,
            ManagementActionRevisionRequest(expected_revision=1, reason="项目优先级调整"),
            actor_id="管理者",
        ),
    )
    assert cancelled.status == "CANCELLED"
    assert cancelled.reported_done is False
    assert cancelled.verified_done is False
    assert cancelled.revision == 2

    active = _call(
        client,
        lambda service: service.list(project_uuid, include_cancelled=False),
    )
    all_actions = _call(
        client,
        lambda service: service.list(project_uuid, include_cancelled=True),
    )
    assert active.total == 0
    assert all_actions.total == 1
    history = _call(client, lambda service: service.history(project_uuid, action.id))
    assert history.items[-1].event_type == "CANCELLED"
    assert history.items[-1].reason == "项目优先级调整"

    with pytest.raises(DomainError) as cancelled_edit:
        _call(
            client,
            lambda service: service.update(
                project_uuid,
                action.id,
                ManagementActionUpdate(title="不应成功", expected_revision=2),
                actor_id="管理者",
            ),
        )
    assert cancelled_edit.value.code == "MANAGEMENT_ACTION_CANCELLED"


def test_event_rows_are_database_append_only(client: TestClient) -> None:
    project_id, action = _create_action(client)
    event_id = _call(
        client,
        lambda service: service.history(UUID(project_id), action.id).items[0].id,
    )
    database = client.app.state.database

    with database.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(ManagementActionEventRow)
                .where(ManagementActionEventRow.id == str(event_id))
                .values(message="伪造历史")
            )
        session.rollback()

    with database.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                delete(ManagementActionEventRow).where(ManagementActionEventRow.id == str(event_id))
            )
        session.rollback()

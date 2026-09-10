from __future__ import annotations

import json
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import ObservationAssertionRow, ObservationConflictRow
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import (
    ObservationConflictCandidate,
    ObservationConflictResolutionKind,
    ObservationConflictResolve,
    ObservationConflictStatus,
    ObservationConflictView,
)
from enterprise_insight_backend.service_utils import json_ready, require_revision


class ObservationConflictService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    def list(
        self,
        project_id: UUID,
        *,
        status: ObservationConflictStatus | None = None,
    ) -> list[ObservationConflictView]:
        self.reconcile(project_id)
        statement = select(ObservationConflictRow).where(
            ObservationConflictRow.project_id == str(project_id)
        )
        if status is not None:
            statement = statement.where(ObservationConflictRow.status == status.value)
        rows = self.session.scalars(
            statement.order_by(ObservationConflictRow.updated_at.desc())
        ).all()
        return [self._view(row) for row in rows]

    def reconcile(self, project_id: UUID) -> None:
        self.portfolio.require_project(project_id)
        assertions = self.session.scalars(
            select(ObservationAssertionRow)
            .where(
                ObservationAssertionRow.project_id == str(project_id),
                ObservationAssertionRow.status == "ACTIVE",
            )
            .order_by(
                ObservationAssertionRow.observed_at.desc(),
                ObservationAssertionRow.created_at.desc(),
                ObservationAssertionRow.id.desc(),
            )
        ).all()
        latest: dict[tuple[str, str, str], ObservationAssertionRow] = {}
        for item in assertions:
            latest.setdefault((item.entity_id, item.field_key, item.source_identity_id), item)
        grouped: dict[tuple[str, str], list[ObservationAssertionRow]] = defaultdict(list)
        for item in latest.values():
            grouped[(item.entity_id, item.field_key)].append(item)
        detected: dict[tuple[str, str], list[str]] = {}
        for key, candidates in grouped.items():
            priority = max(item.authority_priority for item in candidates)
            authoritative = [item for item in candidates if item.authority_priority == priority]
            values = {
                json.dumps(item.value, sort_keys=True, ensure_ascii=False, default=str)
                for item in authoritative
            }
            if len(values) > 1:
                detected[key] = sorted(item.id for item in authoritative)
        existing = {
            (item.entity_id, item.field_key): item
            for item in self.session.scalars(
                select(ObservationConflictRow).where(
                    ObservationConflictRow.project_id == str(project_id)
                )
            ).all()
        }
        for key, candidate_ids in detected.items():
            row = existing.get(key)
            if row is None:
                self.session.add(
                    ObservationConflictRow(
                        project_id=str(project_id),
                        entity_id=key[0],
                        field_key=key[1],
                        candidate_assertion_ids=candidate_ids,
                    )
                )
            elif row.candidate_assertion_ids != candidate_ids:
                row.candidate_assertion_ids = candidate_ids
                row.status = ObservationConflictStatus.OPEN.value
                row.resolution_kind = None
                row.chosen_assertion_id = None
                row.override_value = None
                row.rationale = None
                row.resolved_by = None
                row.revision += 1
        for key, row in existing.items():
            if key not in detected and row.status != ObservationConflictStatus.SUPERSEDED.value:
                row.status = ObservationConflictStatus.SUPERSEDED.value
                row.revision += 1
        self.session.flush()

    def resolve(
        self,
        project_id: UUID,
        conflict_id: UUID,
        payload: ObservationConflictResolve,
    ) -> ObservationConflictView:
        self.reconcile(project_id)
        row = self.require(project_id, conflict_id)
        if row.status == ObservationConflictStatus.SUPERSEDED.value:
            raise DomainError(
                "OBSERVATION_CONFLICT_SUPERSEDED",
                "该冲突已经不再适用。",
                status_code=409,
            )
        require_revision(row.revision, payload.expected_revision, resource="观测冲突")
        if payload.resolution_kind == ObservationConflictResolutionKind.CHOOSE_ASSERTION:
            if (
                payload.chosen_assertion_id is None
                or str(payload.chosen_assertion_id) not in row.candidate_assertion_ids
            ):
                raise DomainError(
                    "CONFLICT_ASSERTION_INVALID",
                    "所选观测不属于当前冲突候选。",
                    status_code=422,
                )
        elif payload.resolution_kind == ObservationConflictResolutionKind.OVERRIDE:
            if payload.override_value is None:
                raise DomainError(
                    "CONFLICT_OVERRIDE_REQUIRED",
                    "人工覆盖必须提供明确值。",
                    status_code=422,
                )
        row.status = ObservationConflictStatus.RESOLVED.value
        row.resolution_kind = payload.resolution_kind.value
        row.chosen_assertion_id = (
            str(payload.chosen_assertion_id) if payload.chosen_assertion_id else None
        )
        row.override_value = json_ready(payload.override_value)
        row.rationale = payload.rationale
        row.resolved_by = payload.resolved_by
        row.revision += 1
        self.session.flush()
        return self._view(row)

    def require(
        self, project_id: UUID | str, conflict_id: UUID | str
    ) -> ObservationConflictRow:
        row = self.session.get(ObservationConflictRow, str(conflict_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "OBSERVATION_CONFLICT_NOT_FOUND", "观测冲突不存在。", status_code=404
            )
        return row

    def _view(self, row: ObservationConflictRow) -> ObservationConflictView:
        assertions = {
            item.id: item
            for item in self.session.scalars(
                select(ObservationAssertionRow).where(
                    ObservationAssertionRow.id.in_(row.candidate_assertion_ids)
                )
            ).all()
        }
        candidates = [
            ObservationConflictCandidate.model_validate(
                {
                    "assertion_id": item.id,
                    "source_identity_id": item.source_identity_id,
                    "value": item.value,
                    "authority_priority": item.authority_priority,
                    "observed_at": item.observed_at,
                }
            )
            for assertion_id in row.candidate_assertion_ids
            if (item := assertions.get(assertion_id)) is not None
        ]
        return ObservationConflictView.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "entity_id": row.entity_id,
                "field_key": row.field_key,
                "candidates": candidates,
                "status": row.status,
                "resolution_kind": row.resolution_kind,
                "chosen_assertion_id": row.chosen_assertion_id,
                "override_value": row.override_value,
                "rationale": row.rationale,
                "resolved_by": row.resolved_by,
                "revision": row.revision,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

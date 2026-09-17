"""Fail-closed reader for reviewed virtual-work context.

This module is intentionally read-only. It never promotes virtual assertions to
formal facts and never performs actions. Callers must resolve the current
published release and its allowed real-entity IDs before invoking the reader.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationVersionRow,
)
from enterprise_insight_backend.virtual_work import (
    AnchorStatus,
    RealAnchorRow,
    VirtualWorkAssertionEvidenceRow,
    VirtualWorkAssertionRow,
    VirtualWorkEdgeRow,
    VirtualWorkEvidenceRow,
    VirtualWorkModelRow,
    VirtualWorkNodeRow,
    VirtualWorkReviewRow,
    VirtualWorkRevisionRow,
)

_OBSERVATION_REF = re.compile(
    r"^observation://(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})/(?P<revision>[1-9][0-9]*)$"
)

_HARD_LIMITS = {
    "models": 20,
    "anchors": 200,
    "nodes": 200,
    "edges": 400,
    "assertions": 400,
    "sources_per_assertion": 10,
    "excerpt_chars": 2_000,
    "json_chars": 5_000,
}
_DEFAULT_LIMITS = {
    "models": 10,
    "anchors": 100,
    "nodes": 100,
    "edges": 200,
    "assertions": 200,
    "sources_per_assertion": 5,
    "excerpt_chars": 800,
    "json_chars": 3_000,
}


def read_reviewed_virtual_work_context(
    observation_session: Session,
    *,
    company_id: UUID | str | None,
    project_id: UUID | str | None,
    release_id: UUID | str | None,
    allowed_real_entity_ids: Iterable[UUID | str] | None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return reviewed, in-scope virtual-work context and its provenance.

    ``allowed_real_entity_ids`` must be resolved from ``release_id`` by the
    trusted caller; this reader never accepts IDs extracted from model output or
    natural language. A bad or empty scope returns no context (fail-closed).
    ``limits`` can lower defaults, but values are always clamped to hard caps.
    """
    result = _empty_result()
    reasons: Counter[str] = Counter()
    truncations: Counter[str] = Counter()

    company = _parse_uuid(company_id)
    project = _parse_uuid(project_id)
    release = _parse_uuid(release_id)
    allowed = _parse_allowed_ids(allowed_real_entity_ids)
    if (
        company is None
        or project is None
        or release is None
        or allowed is None
        or not allowed
        or not isinstance(observation_session, Session)
    ):
        _record(reasons, "invalid_or_empty_scope")
        return _finish(result, reasons, truncations, None, None, None)

    result["company_id"] = str(company)
    result["project_id"] = str(project)
    result["release_id"] = str(release)
    effective_limits = _effective_limits(limits, truncations)

    models = observation_session.scalars(
        select(VirtualWorkModelRow)
        .where(
            VirtualWorkModelRow.company_id == str(company),
            VirtualWorkModelRow.project_id == str(project),
        )
        .order_by(VirtualWorkModelRow.created_at, VirtualWorkModelRow.id)
    ).all()
    result["coverage"]["models_considered"] = len(models)

    for model in models:
        if len(result["models"]) >= effective_limits["models"]:
            _record(truncations, "model_limit")
            break

        revision = _latest_reviewed_revision(observation_session, model)
        if revision is None:
            _record(reasons, "latest_revision_not_reviewed")
            continue

        anchors = observation_session.scalars(
            select(RealAnchorRow)
            .where(
                RealAnchorRow.model_id == model.id,
                RealAnchorRow.revision_id == revision.id,
            )
            .order_by(RealAnchorRow.id)
        ).all()
        node_rows = observation_session.scalars(
            select(VirtualWorkNodeRow)
            .where(
                VirtualWorkNodeRow.model_id == model.id,
                VirtualWorkNodeRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkNodeRow.created_at, VirtualWorkNodeRow.id)
        ).all()
        revision_node_ids = {row.id for row in node_rows}
        matching_anchors = [
            anchor
            for anchor in anchors
            if anchor.status == AnchorStatus.ACTIVE.value
            and anchor.real_release_id == str(release)
            and anchor.real_entity_id in allowed
            and anchor.virtual_node_id in revision_node_ids
        ]
        if not matching_anchors:
            _record(reasons, "no_matching_active_anchor")
            continue
        remaining_anchors = effective_limits["anchors"] - result["coverage"]["anchors_included"]
        if remaining_anchors <= 0:
            _record(truncations, "anchor_limit", len(matching_anchors))
            continue
        if len(matching_anchors) > remaining_anchors:
            eligible_anchor_count = len(matching_anchors)
            matching_anchors = matching_anchors[:remaining_anchors]
            _record(truncations, "anchor_limit", eligible_anchor_count - len(matching_anchors))
        anchored_node_ids = {anchor.virtual_node_id for anchor in matching_anchors}
        edge_rows = observation_session.scalars(
            select(VirtualWorkEdgeRow)
            .where(
                VirtualWorkEdgeRow.model_id == model.id,
                VirtualWorkEdgeRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkEdgeRow.created_at, VirtualWorkEdgeRow.id)
        ).all()
        # A reviewed model can contain several unrelated subgraphs. Only expose
        # the selected real anchors and their direct neighbours, not the whole
        # model merely because one part of it is in scope.
        anchor_edges = [
            row
            for row in edge_rows
            if row.source_node_id in anchored_node_ids or row.target_node_id in anchored_node_ids
        ]
        one_hop_node_ids = set(anchored_node_ids)
        for edge in anchor_edges:
            one_hop_node_ids.add(edge.source_node_id)
            one_hop_node_ids.add(edge.target_node_id)
        node_rows = [row for row in node_rows if row.id in one_hop_node_ids]
        node_rows.sort(key=lambda row: (row.id not in anchored_node_ids, row.created_at, row.id))
        selected_nodes = _take_with_limit(
            node_rows,
            effective_limits["nodes"] - result["coverage"]["nodes_included"],
            truncations,
            "node_limit",
        )
        selected_node_ids = {row.id for row in selected_nodes}
        matching_anchors = [
            anchor for anchor in matching_anchors if anchor.virtual_node_id in selected_node_ids
        ]
        if not matching_anchors:
            _record(reasons, "anchor_node_omitted_by_limit")
            continue
        selected_edges = [
            row
            for row in anchor_edges
            if row.source_node_id in selected_node_ids and row.target_node_id in selected_node_ids
        ]
        selected_edges = _take_with_limit(
            selected_edges,
            effective_limits["edges"] - result["coverage"]["edges_included"],
            truncations,
            "edge_limit",
        )
        selected_edge_ids = {row.id for row in selected_edges}

        anchor_views = [
            {
                "id": anchor.id,
                "reference_uri": _anchor_ref(model.id, revision.version, anchor.id),
                "virtual_node_id": anchor.virtual_node_id,
                "real_entity_id": anchor.real_entity_id,
                "real_release_id": anchor.real_release_id,
                "relation_kind": anchor.relation_kind,
                "support_ref": anchor.support_ref,
            }
            for anchor in matching_anchors
        ]
        assertions = _read_assertions(
            observation_session,
            model.id,
            revision.id,
            selected_node_ids,
            selected_edge_ids,
            effective_limits,
            result["coverage"]["assertions_included"],
            result["coverage"],
            model.company_id,
            model.project_id,
            revision.version,
            reasons,
            truncations,
        )
        revision_ref = _revision_ref(model.id, revision.version)
        result["models"].append(
            {
                "model_id": model.id,
                "revision_id": revision.id,
                "revision_version": revision.version,
                "revision_hash": revision.revision_hash,
                "revision_ref": revision_ref,
                "name": revision.name,
                "description": revision.description,
                "anchors": anchor_views,
                "nodes": [
                    {
                        "id": row.id,
                        "reference_uri": _node_ref(model.id, revision.version, row.id),
                        "node_type": row.node_type,
                        "label": row.label,
                        "properties": _bounded_json(row.properties, effective_limits["json_chars"]),
                        "design_metadata_trust": "HUMAN_REVIEWED_MODEL_NOT_SOURCE_ASSERTION",
                        "field_assertions": assertions.get(("NODE", row.id), []),
                    }
                    for row in selected_nodes
                ],
                "edges": [
                    {
                        "id": row.id,
                        "reference_uri": _edge_ref(model.id, revision.version, row.id),
                        "source_node_id": row.source_node_id,
                        "target_node_id": row.target_node_id,
                        "edge_type": row.edge_type,
                        "label": row.label,
                        "properties": _bounded_json(row.properties, effective_limits["json_chars"]),
                        "design_metadata_trust": "HUMAN_REVIEWED_MODEL_NOT_SOURCE_ASSERTION",
                        "field_assertions": assertions.get(("EDGE", row.id), []),
                    }
                    for row in selected_edges
                ],
            }
        )
        result["coverage"]["assertions_included"] += sum(
            len(node["field_assertions"]) for node in result["models"][-1]["nodes"]
        ) + sum(
            len(edge["field_assertions"]) for edge in result["models"][-1]["edges"]
        )
        result["coverage"]["anchors_included"] += len(anchor_views)
        result["coverage"]["nodes_included"] += len(selected_nodes)
        result["coverage"]["edges_included"] += len(selected_edges)
        if len(selected_nodes) < len(node_rows):
            _record(truncations, "node_limit", len(node_rows) - len(selected_nodes))
        if len(selected_edges) < len(anchor_edges):
            _record(
                truncations,
                "edge_limit_or_unselected_endpoint",
                len(anchor_edges) - len(selected_edges),
            )

    result["coverage"]["models_included"] = len(result["models"])
    return _finish(result, reasons, truncations, company, project, release)


def virtual_work_context_fingerprint(context: dict[str, Any]) -> str:
    """Hash the exact reviewed virtual read set for route-to-context binding."""
    encoded = json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_assertions(
    session: Session,
    model_id: str,
    revision_id: str,
    node_ids: set[str],
    edge_ids: set[str],
    limits: dict[str, int],
    already_included: int,
    coverage: dict[str, Any],
    company_id: str,
    project_id: str,
    revision_version: int,
    reasons: Counter[str],
    truncations: Counter[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    output: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if not node_ids and not edge_ids:
        return output
    rows = session.scalars(
        select(VirtualWorkAssertionRow)
        .where(
            VirtualWorkAssertionRow.model_id == model_id,
            VirtualWorkAssertionRow.revision_id == revision_id,
        )
        .order_by(
            VirtualWorkAssertionRow.subject_kind,
            VirtualWorkAssertionRow.subject_id,
            VirtualWorkAssertionRow.id,
        )
    ).all()
    source_cache: dict[tuple[str, str], tuple[bool, str | None, dict[str, Any] | None]] = {}
    current_count = already_included
    for row in rows:
        subject_ids = node_ids if row.subject_kind == "NODE" else edge_ids
        if row.subject_kind not in {"NODE", "EDGE"} or row.subject_id not in subject_ids:
            continue
        links = session.scalars(
            select(VirtualWorkAssertionEvidenceRow)
            .where(
                VirtualWorkAssertionEvidenceRow.model_id == model_id,
                VirtualWorkAssertionEvidenceRow.revision_id == revision_id,
                VirtualWorkAssertionEvidenceRow.assertion_id == row.id,
            )
            .order_by(VirtualWorkAssertionEvidenceRow.evidence_id)
        ).all()
        if not links:
            _record(reasons, "assertion_without_source")
            continue
        source_views: list[dict[str, Any]] = []
        invalid_reasons: list[str] = []
        for link in links:
            coverage["source_refs_checked"] += 1
            evidence = session.get(
                VirtualWorkEvidenceRow,
                {"model_id": model_id, "revision_id": revision_id, "id": link.evidence_id},
            )
            if evidence is None:
                invalid_reasons.append("evidence_row_missing")
                continue
            source_key = (evidence.source_ref, evidence.excerpt)
            if source_key not in source_cache:
                source_cache[source_key] = _validate_observation_source(
                    session,
                    evidence.source_ref,
                    evidence.excerpt,
                    company_id=company_id,
                    project_id=project_id,
                )
            valid, source_reason, source_identity = source_cache[source_key]
            if not valid:
                coverage["source_refs_stale_or_invalid"] += 1
                invalid_reasons.append(source_reason or "source_not_current")
                continue
            clipped_excerpt = evidence.excerpt[: limits["excerpt_chars"]]
            source_views.append(
                {
                    "source_ref": evidence.source_ref,
                    "reference_uri": evidence.source_ref,
                    "observation_id": source_identity["observation_id"],
                    "revision": source_identity["revision"],
                    "evidence_kind": evidence.evidence_kind,
                    "excerpt": clipped_excerpt,
                    "excerpt_truncated": len(evidence.excerpt) > len(clipped_excerpt),
                }
            )
        if invalid_reasons:
            for invalid_reason in invalid_reasons:
                _record(reasons, invalid_reason)
            _record(reasons, "assertion_excluded_due_to_stale_or_unverifiable_source")
            continue
        if len(links) > limits["sources_per_assertion"]:
            _record(reasons, "assertion_source_limit")
            continue
        if current_count >= limits["assertions"]:
            _record(truncations, "assertion_limit")
            coverage["assertions_truncated"] += 1
            continue
        output.setdefault((row.subject_kind, row.subject_id), []).append(
            {
                "assertion_id": row.id,
                "reference_uri": _assertion_ref(model_id, revision_version, row.id),
                "field_name": row.field_name,
                "value": _bounded_json(row.value_json, limits["json_chars"]),
                "assertion_kind": row.assertion_kind,
                "scope": _bounded_json(row.scope, limits["json_chars"]),
                "valid_from": row.valid_from.isoformat() if row.valid_from else None,
                "valid_to": row.valid_to.isoformat() if row.valid_to else None,
                "method": row.method,
                "source_refs": source_views,
            }
        )
        current_count += 1
    return output


def _validate_observation_source(
    session: Session,
    source_ref: str,
    excerpt: str,
    *,
    company_id: str,
    project_id: str,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    match = _OBSERVATION_REF.fullmatch(source_ref)
    if match is None or not excerpt:
        return False, "source_ref_invalid", None
    observation_id = match.group("id")
    revision = int(match.group("revision"))
    observation = session.get(ManagementObservationRow, observation_id)
    if observation is None:
        return False, "source_observation_missing", None
    if observation.company_id != company_id or observation.project_id != project_id:
        return False, "source_observation_scope_mismatch", None
    if observation.status != "ACTIVE":
        return False, "source_observation_inactive", None
    if observation.revision != revision:
        return False, "source_revision_stale", None
    version = session.scalar(
        select(ObservationVersionRow).where(
            ObservationVersionRow.observation_id == observation_id,
            ObservationVersionRow.revision == revision,
        )
    )
    if version is None or not isinstance(version.snapshot, dict):
        return False, "source_revision_missing", None
    snapshot = version.snapshot
    if snapshot.get("status") != "ACTIVE":
        return False, "source_snapshot_inactive", None
    if not isinstance(snapshot.get("content"), str) or excerpt not in snapshot["content"]:
        return False, "source_excerpt_mismatch", None
    if excerpt not in observation.content:
        return False, "source_excerpt_mismatch", None
    return (
        True,
        None,
        {"observation_id": observation_id, "revision": revision},
    )


def _latest_reviewed_revision(
    session: Session, model: VirtualWorkModelRow
) -> VirtualWorkRevisionRow | None:
    revisions = session.scalars(
        select(VirtualWorkRevisionRow)
        .where(VirtualWorkRevisionRow.model_id == model.id)
        .order_by(VirtualWorkRevisionRow.version.desc())
    ).all()
    for revision in revisions:
        if _is_reviewed_current_revision(session, model, revision):
            return revision
    return None


def _is_reviewed_current_revision(
    session: Session, model: VirtualWorkModelRow, revision: VirtualWorkRevisionRow
) -> bool:
    review = session.scalar(
        select(VirtualWorkReviewRow)
        .where(
            VirtualWorkReviewRow.model_id == model.id,
            VirtualWorkReviewRow.revision_id == revision.id,
        )
        .order_by(VirtualWorkReviewRow.sequence.desc())
        .limit(1)
    )
    if (
        review is None
        or review.decision != "REVIEWED"
        or review.version != revision.version
        or review.revision_hash != revision.revision_hash
    ):
        return False
    scope = review.confirmed_scope
    return (
        isinstance(scope, dict)
        and scope.get("company_id") == model.company_id
        and scope.get("project_id") == model.project_id
        and scope.get("virtual_work_model_id") == model.id
    )


def _revision_ref(model_id: str, version: int) -> str:
    return f"virtual-work://models/{model_id}/revisions/{version}"


def _node_ref(model_id: str, version: int, node_id: str) -> str:
    return f"{_revision_ref(model_id, version)}/nodes/{node_id}"


def _anchor_ref(model_id: str, version: int, anchor_id: str) -> str:
    return f"{_revision_ref(model_id, version)}/anchors/{anchor_id}"


def _edge_ref(model_id: str, version: int, edge_id: str) -> str:
    return f"{_revision_ref(model_id, version)}/edges/{edge_id}"


def _assertion_ref(model_id: str, version: int, assertion_id: str) -> str:
    return f"{_revision_ref(model_id, version)}/assertions/{assertion_id}"


def _effective_limits(
    requested: dict[str, int] | None, truncations: Counter[str]
) -> dict[str, int]:
    values = dict(_DEFAULT_LIMITS)
    if requested is None:
        return values
    for key, value in requested.items():
        if key not in values or not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _record(truncations, "invalid_limit_request")
            values[key] = 0
            continue
        values[key] = min(value, _HARD_LIMITS[key])
        if value > _HARD_LIMITS[key]:
            _record(truncations, f"{key}_hard_cap")
    return values


def _take_with_limit(
    rows: list[Any], remaining: int, truncations: Counter[str], reason: str
) -> list[Any]:
    if remaining <= 0:
        if rows:
            _record(truncations, reason, len(rows))
        return []
    selected = rows[:remaining]
    if len(selected) < len(rows):
        _record(truncations, reason, len(rows) - len(selected))
    return selected


def _bounded_json(value: Any, maximum: int) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return {"truncated": True, "preview": "<non-json value omitted>"}
    if len(encoded) <= maximum:
        return value
    return {"truncated": True, "preview": encoded[:maximum]}


def _parse_uuid(value: UUID | str | None) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    return parsed if str(parsed) == value.lower() else None


def _parse_allowed_ids(values: Iterable[UUID | str] | None) -> set[str] | None:
    if values is None or isinstance(values, (str, bytes)):
        return None
    try:
        parsed = [_parse_uuid(value) for value in values]
    except TypeError:
        return None
    if any(value is None for value in parsed):
        return None
    return {str(value) for value in parsed if value is not None}


def _empty_result() -> dict[str, Any]:
    return {
        "source_kind": "REVIEWED_VIRTUAL_WORK",
        "is_formal_fact": False,
        "actions_executed": False,
        "company_id": None,
        "project_id": None,
        "release_id": None,
        "models": [],
        "read_manifest": {
            "virtual_revision_refs": [],
            "virtual_anchor_refs": [],
            "virtual_node_refs": [],
            "virtual_edge_refs": [],
            "virtual_assertion_refs": [],
            "virtual_evidence_refs": [],
        },
        "coverage": {
            "status": "EMPTY",
            "models_considered": 0,
            "models_included": 0,
            "anchors_included": 0,
            "nodes_included": 0,
            "edges_included": 0,
            "assertions_included": 0,
            "assertions_skipped": 0,
            "assertions_truncated": 0,
            "source_refs_checked": 0,
            "source_refs_stale_or_invalid": 0,
            "truncated": False,
            "skip_reasons": [],
            "truncation_reasons": [],
        },
        "risks": [],
    }


def _finish(
    result: dict[str, Any],
    reasons: Counter[str],
    truncations: Counter[str],
    company: UUID | None,
    project: UUID | None,
    release: UUID | None,
) -> dict[str, Any]:
    coverage = result["coverage"]
    coverage["assertions_included"] = sum(
        len(node["field_assertions"])
        for model in result["models"]
        for node in model["nodes"]
    ) + sum(
        len(edge["field_assertions"])
        for model in result["models"]
        for edge in model["edges"]
    )
    coverage["assertions_skipped"] = (
        reasons.get("assertion_excluded_due_to_stale_or_unverifiable_source", 0)
        + reasons.get("assertion_without_source", 0)
        + reasons.get("assertion_source_limit", 0)
    )
    coverage["skip_reasons"] = [
        {"reason": reason, "count": count}
        for reason, count in sorted(reasons.items())
        if count
    ]
    coverage["truncation_reasons"] = [
        {"reason": reason, "count": count}
        for reason, count in sorted(truncations.items())
        if count and not reason.endswith("_included_count")
    ]
    coverage["truncated"] = bool(truncations)
    if company is None or project is None or release is None:
        coverage["status"] = "EMPTY"
        result["risks"].append({"code": "FAIL_CLOSED_INVALID_SCOPE", "severity": "HIGH"})
    elif truncations or reasons:
        coverage["status"] = "PARTIAL" if result["models"] else "EMPTY"
    else:
        coverage["status"] = "COMPLETE" if result["models"] else "EMPTY"
    if reasons:
        result["risks"].append(
            {
                "code": "SKIPPED_OR_STALE_VIRTUAL_CONTENT",
                "severity": "HIGH",
                "details": coverage["skip_reasons"],
            }
        )
    if truncations:
        result["risks"].append(
            {
                "code": "CONTEXT_TRUNCATED",
                "severity": "MEDIUM",
                "details": coverage["truncation_reasons"],
            }
        )
    read_manifest = result["read_manifest"]
    read_manifest["virtual_revision_refs"] = sorted(
        {model["revision_ref"] for model in result["models"]}
    )
    read_manifest["virtual_node_refs"] = sorted(
        node["reference_uri"] for model in result["models"] for node in model["nodes"]
    )
    read_manifest["virtual_anchor_refs"] = sorted(
        anchor["reference_uri"] for model in result["models"] for anchor in model["anchors"]
    )
    read_manifest["virtual_edge_refs"] = sorted(
        edge["reference_uri"] for model in result["models"] for edge in model["edges"]
    )
    read_manifest["virtual_assertion_refs"] = sorted(
        assertion["reference_uri"]
        for model in result["models"]
        for item in [*model["nodes"], *model["edges"]]
        for assertion in item["field_assertions"]
    )
    read_manifest["virtual_evidence_refs"] = sorted(
        {
            source["reference_uri"]
            for model in result["models"]
            for item in [*model["nodes"], *model["edges"]]
            for assertion in item["field_assertions"]
            for source in assertion["source_refs"]
        }
    )
    return result


def _record(counter: Counter[str], reason: str, count: int = 1) -> None:
    if count > 0:
        counter[reason] += count

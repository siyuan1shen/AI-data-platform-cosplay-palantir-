from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import ClaimRow, EvidenceFragmentRow, SourceDocumentRow
from enterprise_insight_backend.schemas import EvidenceReference


def validate_evidence_references(
    session: Session,
    project_id: UUID | str,
    references: Iterable[EvidenceReference | dict[str, Any]],
) -> None:
    """Validate that every supplied evidence identifier exists in the same project.

    Evidence is optional, but fabricated or internally inconsistent identifiers are not.
    A reference may point to a document, fragment, claim, or a consistent combination.
    """

    expected_project_id = str(project_id)
    for raw_reference in references:
        reference = (
            raw_reference
            if isinstance(raw_reference, EvidenceReference)
            else EvidenceReference.model_validate(raw_reference)
        )
        if not any(
            (reference.source_document_id, reference.fragment_id, reference.claim_id)
        ):
            raise DomainError(
                "EVIDENCE_REFERENCE_EMPTY",
                "证据引用必须至少包含材料、片段或陈述标识。",
                status_code=422,
            )

        document: SourceDocumentRow | None = None
        fragment: EvidenceFragmentRow | None = None
        claim: ClaimRow | None = None

        if reference.source_document_id is not None:
            document = session.get(SourceDocumentRow, str(reference.source_document_id))
            if document is None or document.project_id != expected_project_id:
                raise _invalid_reference("材料不存在或不属于当前项目。", reference)

        if reference.fragment_id is not None:
            fragment = session.get(EvidenceFragmentRow, str(reference.fragment_id))
            fragment_document = (
                session.get(SourceDocumentRow, fragment.source_document_id) if fragment else None
            )
            if (
                fragment is None
                or fragment_document is None
                or fragment_document.project_id != expected_project_id
            ):
                raise _invalid_reference("证据片段不存在或不属于当前项目。", reference)
            if document is not None and fragment.source_document_id != document.id:
                raise _invalid_reference("证据片段不属于所引用的材料。", reference)

        if reference.claim_id is not None:
            claim = session.get(ClaimRow, str(reference.claim_id))
            if claim is None or claim.project_id != expected_project_id:
                raise _invalid_reference("证据陈述不存在或不属于当前项目。", reference)
            if fragment is not None and claim.fragment_id != fragment.id:
                raise _invalid_reference("证据陈述不属于所引用的片段。", reference)
            if document is not None and fragment is None:
                claim_fragment = session.get(EvidenceFragmentRow, claim.fragment_id)
                if claim_fragment is None or claim_fragment.source_document_id != document.id:
                    raise _invalid_reference("证据陈述不属于所引用的材料。", reference)


def _invalid_reference(message: str, reference: EvidenceReference) -> DomainError:
    return DomainError(
        "EVIDENCE_REFERENCE_INVALID",
        message,
        status_code=422,
        details=[{"reference": reference.model_dump(mode="json")}],
    )

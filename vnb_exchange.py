# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned VNB exchange for LEG formation packages."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import community_access
import database as db

FORMATION_SUBMISSION = "formation_submission"
SUPPORTED_CONTRACT_VERSION = "vnb-formation/1"
MEMBERSHIP_CONTRACT_VERSION = "vnb-membership/1"
PARTICIPANT_JOIN = "participant_join"
PARTICIPANT_EXIT = "participant_exit"
PARTICIPANT_CHANGE = "participant_change"
MUTATION_CAPABILITIES = {
    "join": PARTICIPANT_JOIN,
    "exit": PARTICIPANT_EXIT,
    "change": PARTICIPANT_CHANGE,
}
FINAL_STATES = frozenset({"acknowledged", "rejected"})


@dataclass(frozen=True)
class ExchangeContract:
    adapter_key: str
    contract_version: str
    capabilities: frozenset[str]


@dataclass(frozen=True)
class PackageDocument:
    document_id: int
    document_type: str
    filename: str
    content: bytes


@dataclass(frozen=True)
class FormationPackage:
    community_id: str
    documents: tuple[PackageDocument, ...]


@dataclass(frozen=True)
class FormationSubmission:
    community_id: str
    actor_building_id: str


@dataclass(frozen=True)
class ParticipantMutation:
    mutation_id: str
    community_id: str
    participant_id: str
    mutation_type: str
    effective_date: str
    source_agreement_id: str
    before: dict
    after: dict


@dataclass(frozen=True)
class ParticipantMutationSubmission:
    mutation_id: str
    community_id: str
    participant_id: str
    actor_building_id: str
    mutation_type: str
    effective_date: str
    source_agreement_id: str
    after: dict


@dataclass(frozen=True)
class ManualDeliveryConfirmation:
    community_id: str
    case_id: str
    actor_building_id: str


@dataclass(frozen=True)
class DeliveryResult:
    state: str
    request_id: str | None = None
    response_status: str | None = None
    evidence: bytes | None = None
    retryable: bool = False
    failure_code: str | None = None


@dataclass(frozen=True)
class SubmissionClaim:
    community_id: str
    actor_building_id: str
    adapter_key: str
    contract_version: str
    capability_snapshot: tuple[str, ...]
    payload_fingerprint: str


@dataclass(frozen=True)
class SubmissionOutcome:
    case_id: str
    state: str
    contract_version: str
    payload_fingerprint: str
    next_action: str
    request_id: str | None = None
    response_status: str | None = None
    manual_package: bytes | None = None
    evidence: bytes | None = None
    retryable: bool = False
    failure_code: str | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class MutationClaim:
    mutation_id: str
    community_id: str
    participant_id: str
    actor_building_id: str
    mutation_type: str
    effective_date: str
    source_agreement_id: str
    before: dict
    after: dict
    adapter_key: str
    contract_version: str
    capability_snapshot: tuple[str, ...]
    payload_fingerprint: str


@dataclass(frozen=True)
class MutationOutcome(SubmissionOutcome):
    mutation_id: str = ""


class VnbAdapter(Protocol):
    def contract(self) -> ExchangeContract: ...

    def submit(
        self, package: FormationPackage, *, idempotency_key: str
    ) -> DeliveryResult: ...

    def submit_mutation(
        self, mutation: ParticipantMutation, *, idempotency_key: str
    ) -> DeliveryResult: ...


class ManualHandoverAdapter:
    """Declare a tracked handover when no automated VNB transport is configured."""

    def contract(self) -> ExchangeContract:
        return ExchangeContract(
            adapter_key="manual-handover",
            contract_version=SUPPORTED_CONTRACT_VERSION,
            capabilities=frozenset(),
        )

    def submit(
        self, package: FormationPackage, *, idempotency_key: str
    ) -> DeliveryResult:
        raise RuntimeError("Manual handover has no automated transport")


ADAPTERS: dict[str, VnbAdapter] = {"manual-handover": ManualHandoverAdapter()}


def adapter_for(adapter_key: str | None) -> VnbAdapter:
    """Resolve an explicitly configured adapter."""
    key = adapter_key or "manual-handover"
    if key not in ADAPTERS:
        raise KeyError(key)
    return ADAPTERS[key]


class SubmissionCases(Protocol):
    def claim(self, submission: SubmissionClaim) -> dict: ...

    def record_outcome(self, case_id: str, outcome: SubmissionOutcome) -> dict: ...

    def mark_manual_delivered(
        self, community_id: str, case_id: str, actor_building_id: str
    ) -> dict: ...


class FormationSubmissionForbidden(PermissionError):
    """The actor cannot submit formation documents for this community."""


class FormationSubmissionInvalid(RuntimeError):
    """The community or its documents are not ready for submission."""


class ParticipantMutationInvalid(RuntimeError):
    """A participant mutation is invalid or conflicts with pending work."""


def _manifest(package: FormationPackage, contract: ExchangeContract) -> dict:
    documents = [
        {
            "document_id": document.document_id,
            "document_type": document.document_type,
            "filename": document.filename,
            "sha256": hashlib.sha256(document.content).hexdigest(),
        }
        for document in sorted(package.documents, key=lambda item: item.document_id)
    ]
    return {
        "adapter_key": contract.adapter_key,
        "community_id": package.community_id,
        "contract_version": contract.contract_version,
        "documents": documents,
    }


def _fingerprint(manifest: dict) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _manual_package(package: FormationPackage, manifest: dict) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        entries = [
            ("manifest.json", json.dumps(manifest, sort_keys=True, indent=2).encode())
        ]
        entries.extend(
            (f"documents/document-{document.document_id}.pdf", document.content)
            for document in sorted(package.documents, key=lambda item: item.document_id)
        )
        for filename, content in entries:
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def _outcome_from_row(row: dict) -> SubmissionOutcome:
    allowed = SubmissionOutcome.__dataclass_fields__
    return SubmissionOutcome(
        **{key: value for key, value in row.items() if key in allowed}
    )


def _mutation_outcome_from_row(row: dict) -> MutationOutcome:
    allowed = MutationOutcome.__dataclass_fields__
    return MutationOutcome(
        **{key: value for key, value in row.items() if key in allowed}
    )


def _mutation_manifest(
    mutation: ParticipantMutation, contract: ExchangeContract
) -> dict:
    if mutation.mutation_type not in MUTATION_CAPABILITIES:
        raise ValueError("Unsupported participant mutation type")
    if not all(
        (
            mutation.mutation_id,
            mutation.community_id,
            mutation.participant_id,
            mutation.effective_date,
            mutation.source_agreement_id,
        )
    ):
        raise ValueError("Participant mutation identity is incomplete")
    if not mutation.before or not mutation.after:
        raise ValueError("Participant mutation requires before and after facts")
    return {
        "adapter_key": contract.adapter_key,
        "contract_version": MEMBERSHIP_CONTRACT_VERSION,
        "mutation_id": mutation.mutation_id,
        "community_id": mutation.community_id,
        "participant_id": mutation.participant_id,
        "mutation_type": mutation.mutation_type,
        "effective_date": mutation.effective_date,
        "source_agreement_id": mutation.source_agreement_id,
        "before": mutation.before,
        "after": mutation.after,
    }


def _manual_mutation_package(manifest: dict) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, sort_keys=True, indent=2).encode())
    return output.getvalue()


def submit_participant_mutation(
    mutation: ParticipantMutation,
    actor_building_id: str,
    adapter: VnbAdapter,
    cases,
) -> MutationOutcome:
    """Claim and deliver an immutable participant mutation idempotently."""
    contract = adapter.contract()
    manifest = _mutation_manifest(mutation, contract)
    fingerprint = _fingerprint(manifest)
    row = cases.claim_mutation(
        MutationClaim(
            **mutation.__dict__,
            actor_building_id=actor_building_id,
            adapter_key=contract.adapter_key,
            contract_version=MEMBERSHIP_CONTRACT_VERSION,
            capability_snapshot=tuple(sorted(contract.capabilities)),
            payload_fingerprint=fingerprint,
        )
    )
    if row["state"] != "claimed":
        return _mutation_outcome_from_row(row)
    capability = MUTATION_CAPABILITIES[mutation.mutation_type]
    if capability not in contract.capabilities:
        outcome = MutationOutcome(
            case_id=row["case_id"],
            mutation_id=mutation.mutation_id,
            state="prepared",
            contract_version=MEMBERSHIP_CONTRACT_VERSION,
            payload_fingerprint=fingerprint,
            next_action="download_and_deliver",
            manual_package=_manual_mutation_package(manifest),
        )
    else:
        try:
            delivered = adapter.submit_mutation(mutation, idempotency_key=fingerprint)
        except Exception:
            delivered = DeliveryResult(
                state="failed", retryable=True, failure_code="transport_error"
            )
        next_actions = {
            "acknowledged": "none",
            "rejected": "review_rejection",
            "failed": "retry" if delivered.retryable else "contact_vnb",
            "delivered": "await_acknowledgement",
        }
        if delivered.state not in next_actions:
            raise ValueError("Adapter returned an unsupported delivery state")
        if delivered.state in {"acknowledged", "rejected"} and not all(
            (delivered.request_id, delivered.response_status, delivered.evidence)
        ):
            raise ValueError("Adapter mutation response evidence is incomplete")
        if delivered.state == "delivered" and not all(
            (delivered.request_id, delivered.response_status)
        ):
            raise ValueError("Adapter mutation delivery evidence is incomplete")
        if delivered.state == "failed" and not delivered.failure_code:
            raise ValueError("Adapter failure code is missing")
        outcome = MutationOutcome(
            case_id=row["case_id"],
            mutation_id=mutation.mutation_id,
            state=delivered.state,
            contract_version=MEMBERSHIP_CONTRACT_VERSION,
            payload_fingerprint=fingerprint,
            next_action=next_actions[delivered.state],
            request_id=delivered.request_id,
            response_status=delivered.response_status,
            evidence=delivered.evidence,
            retryable=delivered.retryable,
            failure_code=delivered.failure_code,
        )
    return _mutation_outcome_from_row(
        cases.record_mutation_outcome(row["case_id"], outcome)
    )


def submit_membership_mutation(
    command: ParticipantMutationSubmission,
    adapter: VnbAdapter | None = None,
    *,
    cases=None,
) -> MutationOutcome:
    """Authorize a mutation and snapshot the LEG record without changing it."""
    community = db.fetch_community_with_members(command.community_id)
    actor = next(
        (
            member
            for member in (community or {}).get("members") or []
            if member.get("building_id") == command.actor_building_id
            and member.get("status") == "confirmed"
        ),
        None,
    )
    if not community_access.allows(actor, community_access.MANAGE_MEMBERS):
        raise FormationSubmissionForbidden
    participant = next(
        (
            member
            for member in community.get("members") or []
            if member.get("building_id") == command.participant_id
        ),
        None,
    )
    if not participant:
        raise ParticipantMutationInvalid(
            "Teilnehmende Person gehört nicht zu dieser LEG."
        )
    try:
        date.fromisoformat(command.effective_date)
    except ValueError as error:
        raise ParticipantMutationInvalid("Wirksamkeitsdatum ist ungültig.") from error
    if command.mutation_type == "join" and participant.get("status") != "invited":
        raise ParticipantMutationInvalid("Beitritt setzt eine offene Einladung voraus.")
    if command.mutation_type == "exit" and participant.get("status") != "confirmed":
        raise ParticipantMutationInvalid(
            "Austritt setzt eine bestätigte Teilnahme voraus."
        )
    before = {
        key: participant.get(key)
        for key in ("building_id", "status", "role", "access_roles")
        if key in participant
    }
    after = dict(before)
    if command.mutation_type == "join":
        after["status"] = "confirmed"
    elif command.mutation_type == "exit":
        after["status"] = "exited"
    elif command.mutation_type == "change":
        if not command.after:
            raise ParticipantMutationInvalid("Änderungsdaten fehlen.")
        unknown = set(command.after) - set(before)
        if unknown:
            raise ParticipantMutationInvalid(
                "Änderungsdaten enthalten unbekannte Felder: "
                + ", ".join(sorted(unknown))
            )
        after.update(command.after)
    else:
        raise ParticipantMutationInvalid("Mutationstyp ist unbekannt.")
    if adapter is None:
        try:
            adapter = adapter_for(community.get("vnb_adapter_key"))
        except KeyError as error:
            raise ParticipantMutationInvalid(
                "Konfigurierter VNB-Adapter fehlt."
            ) from error
    if cases is None:
        from store import vnb_exchange as cases
    return submit_participant_mutation(
        ParticipantMutation(
            mutation_id=command.mutation_id,
            community_id=command.community_id,
            participant_id=command.participant_id,
            mutation_type=command.mutation_type,
            effective_date=command.effective_date,
            source_agreement_id=command.source_agreement_id,
            before=before,
            after=after,
        ),
        command.actor_building_id,
        adapter,
        cases,
    )


def submit_package(
    package: FormationPackage,
    actor_building_id: str,
    adapter: VnbAdapter,
    cases: SubmissionCases,
) -> SubmissionOutcome:
    """Claim and deliver one immutable formation package idempotently."""
    contract = adapter.contract()
    if contract.contract_version != SUPPORTED_CONTRACT_VERSION:
        raise ValueError("Unsupported VNB exchange contract version")
    if not package.documents:
        raise ValueError("Formation package has no documents")

    manifest = _manifest(package, contract)
    fingerprint = _fingerprint(manifest)
    row = cases.claim(
        SubmissionClaim(
            community_id=package.community_id,
            actor_building_id=actor_building_id,
            adapter_key=contract.adapter_key,
            contract_version=contract.contract_version,
            capability_snapshot=tuple(sorted(contract.capabilities)),
            payload_fingerprint=fingerprint,
        )
    )
    if row["state"] != "claimed":
        return _outcome_from_row(row)

    if FORMATION_SUBMISSION not in contract.capabilities:
        outcome = SubmissionOutcome(
            case_id=row["case_id"],
            state="prepared",
            contract_version=contract.contract_version,
            payload_fingerprint=fingerprint,
            next_action="download_and_deliver",
            manual_package=_manual_package(package, manifest),
        )
    else:
        try:
            delivered = adapter.submit(package, idempotency_key=fingerprint)
        except Exception:
            delivered = DeliveryResult(
                state="failed", retryable=True, failure_code="transport_error"
            )
        next_actions = {
            "acknowledged": "none",
            "rejected": "review_rejection",
            "failed": "retry" if delivered.retryable else "contact_vnb",
            "delivered": "await_acknowledgement",
        }
        if delivered.state not in next_actions:
            raise ValueError("Adapter returned an unsupported delivery state")
        if delivered.state == "acknowledged" and not all(
            (delivered.request_id, delivered.response_status, delivered.evidence)
        ):
            raise ValueError("Adapter acknowledgement evidence is incomplete")
        if delivered.state == "delivered" and not all(
            (delivered.request_id, delivered.response_status)
        ):
            raise ValueError("Adapter delivery evidence is incomplete")
        if delivered.state == "rejected" and not all(
            (delivered.request_id, delivered.response_status, delivered.evidence)
        ):
            raise ValueError("Adapter rejection evidence is incomplete")
        if delivered.state == "failed" and not delivered.failure_code:
            raise ValueError("Adapter failure code is missing")
        outcome = SubmissionOutcome(
            case_id=row["case_id"],
            state=delivered.state,
            contract_version=contract.contract_version,
            payload_fingerprint=fingerprint,
            next_action=next_actions[delivered.state],
            request_id=delivered.request_id,
            response_status=delivered.response_status,
            evidence=delivered.evidence,
            retryable=delivered.retryable,
            failure_code=delivered.failure_code,
        )
    return _outcome_from_row(cases.record_outcome(row["case_id"], outcome))


def submit_formation(
    command: FormationSubmission,
    adapter: VnbAdapter | None = None,
    *,
    cases: SubmissionCases | None = None,
) -> SubmissionOutcome:
    """Authorize, assemble and submit one community's signed formation bundle."""
    community = db.fetch_community_with_members(command.community_id)
    if not community:
        raise FormationSubmissionInvalid("Community was not found")
    actor = next(
        (
            member
            for member in community.get("members") or []
            if member.get("building_id") == command.actor_building_id
            and member.get("status") == "confirmed"
        ),
        None,
    )
    if not community_access.allows(actor, community_access.MANAGE_DOCUMENTS):
        raise FormationSubmissionForbidden
    if community.get("status") != "signatures_pending":
        raise FormationSubmissionInvalid("Formation signatures are not complete")

    metadata = db.list_leg_documents(command.community_id)
    if not metadata or any(
        document.get("signing_status") != "completed" for document in metadata
    ):
        raise FormationSubmissionInvalid("Formation documents are not fully signed")
    if adapter is None:
        try:
            adapter = adapter_for(community.get("vnb_adapter_key"))
        except KeyError as error:
            raise FormationSubmissionInvalid(
                "Configured VNB adapter is unavailable"
            ) from error
    documents = []
    for item in metadata:
        stored = db.get_leg_document(item["id"])
        if (
            not stored
            or stored.get("community_id", command.community_id) != command.community_id
        ):
            raise FormationSubmissionInvalid("Formation document was not found")
        documents.append(
            PackageDocument(
                document_id=item["id"],
                document_type=item["doc_type"],
                filename=item["filename"],
                content=stored["pdf_data"],
            )
        )
    if cases is None:
        from store import vnb_exchange as cases

    return submit_package(
        FormationPackage(command.community_id, tuple(documents)),
        command.actor_building_id,
        adapter,
        cases,
    )


def mark_manual_delivered(
    command: ManualDeliveryConfirmation,
    *,
    cases: SubmissionCases | None = None,
) -> SubmissionOutcome:
    """Confirm a manual handover and its guarded formation transition."""
    community = db.fetch_community_with_members(command.community_id)
    actor = next(
        (
            member
            for member in (community or {}).get("members") or []
            if member.get("building_id") == command.actor_building_id
            and member.get("status") == "confirmed"
        ),
        None,
    )
    if not community_access.allows(actor, community_access.MANAGE_DOCUMENTS):
        raise FormationSubmissionForbidden
    if cases is None:
        from store import vnb_exchange as cases
    row = cases.mark_manual_delivered(
        command.community_id, command.case_id, command.actor_building_id
    )
    return _outcome_from_row(row)

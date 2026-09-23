# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public contract for VNB formation exchange (#607)."""

import io
import zipfile
from dataclasses import dataclass

import pytest

import vnb_exchange


@dataclass
class RecordingAdapter:
    capabilities: frozenset[str]
    calls: int = 0

    def contract(self):
        return vnb_exchange.ExchangeContract(
            adapter_key="test-vnb",
            contract_version="vnb-formation/1",
            capabilities=self.capabilities,
        )

    def submit(self, package, *, idempotency_key):
        self.calls += 1
        assert len(idempotency_key) == 64
        return vnb_exchange.DeliveryResult(
            state="acknowledged",
            request_id="request-42",
            response_status="accepted",
            evidence=b"accepted",
        )


class RaisingAdapter(RecordingAdapter):
    def submit(self, package, *, idempotency_key):
        self.calls += 1
        raise TimeoutError("private transport detail")


class InMemoryCases:
    def __init__(self):
        self.saved = {}

    def claim(self, submission):
        key = (
            submission.community_id,
            submission.contract_version,
            submission.payload_fingerprint,
        )
        if key not in self.saved:
            self.saved[key] = {
                "case_id": "case-1",
                "state": "claimed",
                **submission.__dict__,
            }
        elif self.saved[key]["state"] == "failed" and self.saved[key].get("retryable"):
            self.saved[key]["state"] = "claimed"
        return self.saved[key]

    def record_outcome(self, case_id, outcome):
        current = next(row for row in self.saved.values() if row["case_id"] == case_id)
        current.update(outcome.__dict__)
        return current

    def mark_manual_delivered(self, community_id, case_id, actor_building_id):
        current = next(row for row in self.saved.values() if row["case_id"] == case_id)
        assert current["community_id"] == community_id
        current.update(
            state="delivered",
            delivered_by=actor_building_id,
            next_action="await_acknowledgement",
        )
        return current


def _package():
    return vnb_exchange.FormationPackage(
        community_id="community-1",
        documents=(
            vnb_exchange.PackageDocument(
                document_id=7,
                document_type="dso_notification",
                filename="anmeldung.pdf",
                content=b"signed formation",
            ),
        ),
    )


def test_supported_adapter_submits_the_versioned_package_once():
    adapter = RecordingAdapter(frozenset({vnb_exchange.FORMATION_SUBMISSION}))
    cases = InMemoryCases()

    first = vnb_exchange.submit_package(_package(), "actor-1", adapter, cases)
    replay = vnb_exchange.submit_package(_package(), "actor-1", adapter, cases)

    assert first.state == "acknowledged"
    assert first.contract_version == "vnb-formation/1"
    assert first.request_id == "request-42"
    assert replay.case_id == first.case_id
    assert adapter.calls == 1


def test_unsupported_adapter_prepares_manual_handover_without_transport_call():
    adapter = RecordingAdapter(frozenset())

    outcome = vnb_exchange.submit_package(
        _package(), "actor-1", adapter, InMemoryCases()
    )

    assert outcome.state == "prepared"
    assert outcome.next_action == "download_and_deliver"
    assert outcome.manual_package
    assert adapter.calls == 0


def test_manual_package_uses_safe_unique_archive_names():
    package = vnb_exchange.FormationPackage(
        community_id="community-1",
        documents=(
            vnb_exchange.PackageDocument(7, "agreement", "../vertrag.pdf", b"one"),
            vnb_exchange.PackageDocument(8, "contract", "vertrag.pdf", b"two"),
        ),
    )

    outcome = vnb_exchange.submit_package(
        package, "actor-1", RecordingAdapter(frozenset()), InMemoryCases()
    )

    with zipfile.ZipFile(io.BytesIO(outcome.manual_package)) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "documents/document-7.pdf",
            "documents/document-8.pdf",
        ]


def test_transport_exception_becomes_actionable_failed_outcome():
    adapter = RaisingAdapter(frozenset({vnb_exchange.FORMATION_SUBMISSION}))
    cases = InMemoryCases()
    outcome = vnb_exchange.submit_package(
        _package(),
        "actor-1",
        adapter,
        cases,
    )

    assert outcome.state == "failed"
    assert outcome.retryable is True
    assert outcome.failure_code == "transport_error"
    assert outcome.next_action == "retry"
    replay = vnb_exchange.submit_package(_package(), "actor-1", adapter, cases)
    assert replay.case_id == outcome.case_id
    assert adapter.calls == 2


def test_acknowledgement_requires_request_status_and_evidence():
    adapter = RecordingAdapter(frozenset({vnb_exchange.FORMATION_SUBMISSION}))
    adapter.submit = lambda _package, **_kwargs: vnb_exchange.DeliveryResult(
        state="acknowledged"
    )

    with pytest.raises(ValueError, match="acknowledgement evidence"):
        vnb_exchange.submit_package(_package(), "actor-1", adapter, InMemoryCases())


def test_rejection_is_visible_with_evidence_and_next_action():
    adapter = RecordingAdapter(frozenset({vnb_exchange.FORMATION_SUBMISSION}))
    adapter.submit = lambda _package, **_kwargs: vnb_exchange.DeliveryResult(
        state="rejected",
        request_id="request-42",
        response_status="invalid",
        evidence=b"reason",
    )

    outcome = vnb_exchange.submit_package(
        _package(), "actor-1", adapter, InMemoryCases()
    )

    assert outcome.state == "rejected"
    assert outcome.next_action == "review_rejection"
    assert outcome.response_status == "invalid"


def test_submit_formation_loads_the_authorized_signed_bundle(monkeypatch):
    adapter = RecordingAdapter(frozenset())
    cases = InMemoryCases()
    monkeypatch.setattr(
        vnb_exchange.db,
        "fetch_community_with_members",
        lambda community_id: {
            "community_id": community_id,
            "status": "signatures_pending",
            "members": [
                {
                    "building_id": "actor-1",
                    "status": "confirmed",
                    "access_roles": ["documents"],
                }
            ],
        },
    )
    monkeypatch.setattr(
        vnb_exchange.db,
        "list_leg_documents",
        lambda community_id: [
            {
                "id": 7,
                "doc_type": "dso_notification",
                "filename": "anmeldung.pdf",
                "signing_status": "completed",
            }
        ],
    )
    monkeypatch.setattr(
        vnb_exchange.db,
        "get_leg_document",
        lambda document_id: {"id": document_id, "pdf_data": b"signed formation"},
    )

    outcome = vnb_exchange.submit_formation(
        vnb_exchange.FormationSubmission("community-1", "actor-1"),
        adapter,
        cases=cases,
    )

    assert outcome.state == "prepared"
    assert adapter.calls == 0


def test_submit_formation_denies_cross_community_actor(monkeypatch):
    monkeypatch.setattr(
        vnb_exchange.db,
        "fetch_community_with_members",
        lambda _community_id: {
            "status": "signatures_pending",
            "members": [{"building_id": "someone-else", "access_roles": ["admin"]}],
        },
    )

    with pytest.raises(vnb_exchange.FormationSubmissionForbidden):
        vnb_exchange.submit_formation(
            vnb_exchange.FormationSubmission("community-1", "actor-1"),
            RecordingAdapter(frozenset()),
            cases=InMemoryCases(),
        )


def test_authorized_actor_marks_the_manual_case_delivered(monkeypatch):
    adapter = RecordingAdapter(frozenset())
    cases = InMemoryCases()
    community = {
        "community_id": "community-1",
        "status": "signatures_pending",
        "members": [
            {
                "building_id": "actor-1",
                "status": "confirmed",
                "access_roles": ["documents"],
            }
        ],
    }
    monkeypatch.setattr(
        vnb_exchange.db, "fetch_community_with_members", lambda _community_id: community
    )
    prepared = vnb_exchange.submit_package(_package(), "actor-1", adapter, cases)

    delivered = vnb_exchange.mark_manual_delivered(
        vnb_exchange.ManualDeliveryConfirmation(
            "community-1", prepared.case_id, "actor-1"
        ),
        cases=cases,
    )

    assert delivered.state == "delivered"
    assert delivered.next_action == "await_acknowledgement"


class MutationCases:
    def __init__(self):
        self.rows = {}

    def claim_mutation(self, claim):
        key = (claim.community_id, claim.mutation_id)
        if key not in self.rows:
            self.rows[key] = {
                "case_id": "mutation-case-1",
                "state": "claimed",
                **claim.__dict__,
            }
        return self.rows[key]

    def record_mutation_outcome(self, case_id, outcome):
        row = next(value for value in self.rows.values() if value["case_id"] == case_id)
        row.update(outcome.__dict__)
        return row


class MutationAdapter(RecordingAdapter):
    def submit_mutation(self, mutation, *, idempotency_key):
        self.calls += 1
        return vnb_exchange.DeliveryResult(
            state="acknowledged",
            request_id="mutation-42",
            response_status="accepted",
            evidence=b"accepted",
        )


def _mutation(kind="join"):
    return vnb_exchange.ParticipantMutation(
        mutation_id="membership-2026-001",
        community_id="community-1",
        participant_id="building-2",
        mutation_type=kind,
        effective_date="2026-10-01",
        source_agreement_id="agreement-v3",
        before={"status": "invited"} if kind == "join" else {"status": "confirmed"},
        after={"status": "confirmed"} if kind == "join" else {"status": "exited"},
    )


def test_membership_join_is_versioned_idempotent_and_keeps_before_after_facts():
    adapter = MutationAdapter(frozenset({vnb_exchange.PARTICIPANT_JOIN}))
    cases = MutationCases()

    first = vnb_exchange.submit_participant_mutation(
        _mutation(), "actor-1", adapter, cases
    )
    replay = vnb_exchange.submit_participant_mutation(
        _mutation(), "actor-1", adapter, cases
    )

    assert first.state == "acknowledged"
    assert first.mutation_id == "membership-2026-001"
    assert first.contract_version == "vnb-membership/1"
    assert replay.case_id == first.case_id
    assert adapter.calls == 1


def test_unsupported_exit_uses_the_shared_manual_handover():
    adapter = MutationAdapter(frozenset())
    outcome = vnb_exchange.submit_participant_mutation(
        _mutation("exit"), "actor-1", adapter, MutationCases()
    )
    assert outcome.state == "prepared"
    assert outcome.next_action == "download_and_deliver"
    assert outcome.manual_package
    assert adapter.calls == 0


def test_mutation_rejects_invalid_or_lossy_facts():
    mutation = _mutation()
    object.__setattr__(mutation, "after", {})
    with pytest.raises(ValueError, match="before and after"):
        vnb_exchange.submit_participant_mutation(
            mutation, "actor-1", MutationAdapter(frozenset()), MutationCases()
        )


def test_authenticated_membership_flow_is_community_scoped(monkeypatch):
    monkeypatch.setattr(
        vnb_exchange.db,
        "fetch_community_with_members",
        lambda cid: {
            "community_id": cid,
            "vnb_adapter_key": "manual-handover",
            "members": [
                {
                    "building_id": "operator",
                    "status": "confirmed",
                    "access_roles": ["membership"],
                },
                {"building_id": "participant", "status": "invited", "role": "member"},
            ],
        },
    )
    outcome = vnb_exchange.submit_membership_mutation(
        vnb_exchange.ParticipantMutationSubmission(
            "mutation-1",
            "community-1",
            "participant",
            "operator",
            "join",
            "2026-10-01",
            "agreement-v3",
            {},
        ),
        cases=MutationCases(),
    )
    assert outcome.state == "prepared"

    with pytest.raises(vnb_exchange.FormationSubmissionForbidden):
        vnb_exchange.submit_membership_mutation(
            vnb_exchange.ParticipantMutationSubmission(
                "mutation-2",
                "community-1",
                "participant",
                "outsider",
                "join",
                "2026-10-01",
                "agreement-v3",
                {},
            ),
            cases=MutationCases(),
        )


def test_invited_membership_operator_cannot_submit_mutation(monkeypatch):
    monkeypatch.setattr(
        vnb_exchange.db,
        "fetch_community_with_members",
        lambda cid: {
            "community_id": cid,
            "vnb_adapter_key": "manual-handover",
            "members": [
                {
                    "building_id": "operator",
                    "status": "invited",
                    "access_roles": ["membership"],
                },
                {"building_id": "participant", "status": "invited", "role": "member"},
            ],
        },
    )

    with pytest.raises(vnb_exchange.FormationSubmissionForbidden):
        vnb_exchange.submit_membership_mutation(
            vnb_exchange.ParticipantMutationSubmission(
                "mutation-1",
                "community-1",
                "participant",
                "operator",
                "join",
                "2026-10-01",
                "agreement-v3",
                {},
            ),
            cases=MutationCases(),
        )


def test_mutation_rejection_keeps_leg_facts_and_evidence():
    adapter = MutationAdapter(frozenset({vnb_exchange.PARTICIPANT_EXIT}))
    adapter.submit_mutation = lambda *_args, **_kwargs: vnb_exchange.DeliveryResult(
        state="rejected",
        request_id="request-9",
        response_status="bad-date",
        evidence=b"VNB rejection",
    )
    cases = MutationCases()
    outcome = vnb_exchange.submit_participant_mutation(
        _mutation("exit"), "actor-1", adapter, cases
    )
    stored = next(iter(cases.rows.values()))
    assert outcome.state == "rejected"
    assert outcome.next_action == "review_rejection"
    assert stored["before"] == {"status": "confirmed"}
    assert stored["after"] == {"status": "exited"}

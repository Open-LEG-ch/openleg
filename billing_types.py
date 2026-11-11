# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persisted approval shapes. Runtime validators still reject malformed rows."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import date
from decimal import Decimal
from typing import Protocol, TypedDict


class BillingCursor(Protocol):
    def execute(self, query: str, params: Sequence[object] = ()) -> object: ...
    def fetchone(self) -> Mapping[str, object] | None: ...
    def fetchall(self) -> Sequence[Mapping[str, object]]: ...


class BillingConnection(Protocol):
    def cursor(self) -> AbstractContextManager[BillingCursor]: ...


class LineItem(TypedDict):
    id: int
    participant_id: str
    item_type: str
    amount_chf: object
    quantity_kwh: object
    unit_price_chf_per_kwh: object


class BatteryConfig(TypedDict):
    community_id: object
    participant_id: str
    annual_cost_chf: Decimal
    capacity_kwh: Decimal
    shares: dict[str, Decimal]


class BatterySnapshot(TypedDict):
    energy: dict[str, object]
    attribution: dict[str, dict[str, object]]
    period_start: object
    period_end: object
    period_fraction: object


class SourceMetadata(TypedDict):
    document_status: object
    evidence_sha256: object
    sender_id: object
    receiver_id: object
    document_created_at: object
    vnb_community_id: object
    precision: object
    quality_policy: object
    channel_balance_policy: object
    products: object
    mappings: list[dict[str, object]]


class SourceEvidence(TypedDict):
    metadata: SourceMetadata


class Reconciliation(TypedDict):
    participant_registrations: dict[str, str]
    precision_policy: dict[str, object]
    source_evidence: SourceEvidence
    per_participant: dict[str, dict[str, object]]
    production_per_participant: dict[str, dict[str, object]]


class Period(TypedDict):
    status: str
    community_id: str
    period_start: date | str
    period_end: date | str
    input_fingerprint: str
    source_document_ids: list[str]
    billing_policy_snapshot: dict[str, object]
    line_items: list[LineItem]
    reconciliation: Reconciliation
    battery_snapshot: BatterySnapshot | None
    calculated_values_fingerprint: str | None
    prepared_by: str | None


class InvoiceSnapshot(TypedDict):
    participant_id: str
    line_items_snapshot: object
    net_chf: Decimal
    vat_rate_pct: Decimal
    vat_chf: Decimal
    gross_chf: Decimal
    issue_date: date
    due_date: date
    policy_snapshot: dict[str, object]
    provenance_snapshot: dict[str, object]
    input_fingerprint: str
    source_document_ids: object


class ValidatedPolicy(TypedDict):
    vat_rate_pct: Decimal
    payment_days: int
    internal_price_chf_per_kwh: Decimal
    settlement_fee_chf_per_kwh: Decimal

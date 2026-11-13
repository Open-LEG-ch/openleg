# SPDX-License-Identifier: AGPL-3.0-or-later
"""Closed public domain error reasons."""

from enum import StrEnum


class ErrorCode(StrEnum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    INVALID_CREDENTIAL = "invalid_credential"
    CAPABILITY_DENIED = "capability_denied"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    INVALID_PAGINATION = "invalid_pagination"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    INVALID_FORMATION = "invalid_formation"
    INVALID_MEMBERSHIP = "invalid_membership"
    INVALID_CASE = "invalid_case"
    INVALID_PAYMENT = "invalid_payment"
    CONFLICT = "conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    RETRY_IN_PROGRESS = "retry_in_progress"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


class IdempotencyConflict(ValueError):
    reason = ErrorCode.IDEMPOTENCY_CONFLICT

# SPDX-License-Identifier: AGPL-3.0-or-later
"""Community authorization policy.

Callers ask for one capability.  Legacy ``admin`` memberships retain full
access while scoped roles let a community delegate a narrower responsibility.
"""

from i18n import gettext as _
from translation_markers import gettext_noop as _N

ADMIN = "admin"
MEMBERSHIP = "membership"
DOCUMENTS = "documents"
METERING = "metering"
BILLING_PREPARER = "billing_preparer"
BILLING_APPROVER = "billing_approver"
AUDITOR = "auditor"

ROLES = frozenset(
    {
        ADMIN,
        MEMBERSHIP,
        DOCUMENTS,
        METERING,
        BILLING_PREPARER,
        BILLING_APPROVER,
        AUDITOR,
    }
)

VIEW_COMMUNITY = "community.view"
MANAGE_MEMBERS = "membership.manage"
MANAGE_DOCUMENTS = "documents.manage"
MANAGE_METERING = "metering.manage"
PREPARE_BILLING = "billing.prepare"
APPROVE_BILLING = "billing.approve"
AUDIT_BILLING = "billing.audit"

_CAPABILITIES = {
    ADMIN: frozenset(
        {
            VIEW_COMMUNITY,
            MANAGE_MEMBERS,
            MANAGE_DOCUMENTS,
            MANAGE_METERING,
            PREPARE_BILLING,
            APPROVE_BILLING,
            AUDIT_BILLING,
        }
    ),
    MEMBERSHIP: frozenset({VIEW_COMMUNITY, MANAGE_MEMBERS}),
    DOCUMENTS: frozenset({VIEW_COMMUNITY, MANAGE_DOCUMENTS}),
    METERING: frozenset({VIEW_COMMUNITY, MANAGE_METERING}),
    BILLING_PREPARER: frozenset({VIEW_COMMUNITY, PREPARE_BILLING, AUDIT_BILLING}),
    BILLING_APPROVER: frozenset({VIEW_COMMUNITY, APPROVE_BILLING, AUDIT_BILLING}),
    AUDITOR: frozenset({VIEW_COMMUNITY, AUDIT_BILLING}),
}

ROLE_LABELS = {
    ADMIN: _N("Administration"),
    MEMBERSHIP: _N("Mitgliederverwaltung"),
    DOCUMENTS: _N("Dokumente"),
    METERING: _N("Messdaten"),
    BILLING_PREPARER: _N("Abrechnung vorbereiten"),
    BILLING_APPROVER: _N("Abrechnung freigeben"),
    AUDITOR: _N("Prüfung"),
}


def roles_for(member: dict | None) -> frozenset[str]:
    """Return validated roles, including the legacy membership role."""
    if not member:
        return frozenset()
    raw = member.get("access_roles") or []
    if isinstance(raw, str):
        raw = [raw]
    roles = {value for value in raw if value in ROLES}
    if member.get("role") == ADMIN:
        roles.add(ADMIN)
    return frozenset(roles)


def allows(member: dict | None, capability: str, *, confirmed: bool = True) -> bool:
    """Return whether one membership grants a capability."""
    if not member or (confirmed and member.get("status") != "confirmed"):
        return False
    return any(capability in _CAPABILITIES[role] for role in roles_for(member))


def is_administrator(member: dict | None) -> bool:
    """Return whether one membership carries administrator authority."""
    return ADMIN in roles_for(member)


def capabilities_for(member: dict | None) -> frozenset[str]:
    """Return all capabilities granted to one confirmed membership."""
    if not member or member.get("status") != "confirmed":
        return frozenset()
    return frozenset(
        capability for role in roles_for(member) for capability in _CAPABILITIES[role]
    )


def validate_roles(roles) -> tuple[str, ...]:
    """Validate and canonicalise a role collection for persistence."""
    if isinstance(roles, str) or roles is None:
        raise ValueError(_N("Rollen müssen als Liste angegeben werden."))
    values = tuple(sorted(set(roles)))
    unknown = set(values) - ROLES
    if unknown:
        raise ValueError(_N("Unbekannte LEG-Rolle: ") + ", ".join(sorted(unknown)))
    return values


def localize_role_error(message: str) -> str:
    """Present an error while preserving role identifiers and canonical values."""
    prefix = "Unbekannte LEG-Rolle: "
    if message.startswith(prefix):
        return _("Unbekannte LEG-Rolle: %(roles)s", roles=message[len(prefix) :])
    return _(message)

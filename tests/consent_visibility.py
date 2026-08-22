# SPDX-License-Identifier: AGPL-3.0-or-later
"""One home for the question every consent test actually has to answer.

CLAUDE.md states it: "A fake that accepts the *shape* of a query as proof of
its behaviour proves nothing." A substring test for ``JOIN consents`` cannot
tell an inner join from an outer one, and an outer join carrying the predicate
in its ``ON`` clause keeps every building, consented or not.

``filters_by_consent`` answers, for a query as written, whether Postgres would
really drop a building whose neighbour sharing is revoked or absent. Doubles
call it instead of matching substrings, so a rewritten join changes what the
double returns rather than sliding past it.
"""

import re

_CONSENT_JOIN = re.compile(r"\bJOIN\s+consents\b", re.IGNORECASE)
_OUTER_CONSENT_JOIN = re.compile(
    r"\b(LEFT|RIGHT|FULL)\s+(OUTER\s+)?JOIN\s+consents\b", re.IGNORECASE
)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_PREDICATE = "share_with_neighbors = TRUE"


def filters_by_consent(query: str) -> bool:
    """True when this query would exclude a revoked or missing consent row."""
    normalized = " ".join(query.split())
    if not _CONSENT_JOIN.search(normalized):
        return False
    if _OUTER_CONSENT_JOIN.search(normalized):
        parts = _WHERE.split(normalized, maxsplit=1)
        return len(parts) == 2 and _PREDICATE in parts[1]
    return _PREDICATE in normalized

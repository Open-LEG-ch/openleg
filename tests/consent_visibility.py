# SPDX-License-Identifier: AGPL-3.0-or-later
"""One home for the question every consent test actually has to answer.

CLAUDE.md states it: "A fake that accepts the *shape* of a query as proof of
its behaviour proves nothing." A substring test for ``JOIN consents`` cannot
tell an inner join from an outer one; an outer join carrying the predicate in
its ``ON`` clause keeps every building, consented or not; and a predicate sitting
inside an ``OR`` filters nothing at all.

``filters_by_consent`` answers, for a query as written, whether Postgres would
really drop a building whose neighbour sharing is revoked, absent, or unknown.
It reads the predicate as a conjunctive term, not as a substring:

- the consent join must exist, and an outer join only counts when the predicate
  sits in the ``WHERE`` clause rather than in ``ON``
- the predicate must be one of the top-level ``AND`` terms of that clause, so a
  disjunction such as ``share_with_neighbors = TRUE OR share_with_neighbors IS
  NULL`` reads as no filter, which is what it is

Doubles call this instead of matching substrings, so a rewritten join changes
what the double returns rather than sliding past it.
"""

import re

_CONSENT_JOIN = re.compile(r"\bJOIN\s+consents\b", re.IGNORECASE)
_OUTER_CONSENT_JOIN = re.compile(
    r"\b(LEFT|RIGHT|FULL)\s+(OUTER\s+)?JOIN\s+consents\b", re.IGNORECASE
)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_AND = re.compile(r"\bAND\b", re.IGNORECASE)
_TRAILING_CLAUSE = re.compile(
    r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|RETURNING|UNION)\b", re.IGNORECASE
)
_PREDICATE = re.compile(
    r"^\(*\s*\w*\.?share_with_neighbors\s*=\s*TRUE\s*\)*$", re.IGNORECASE
)


def _top_level_terms(clause: str) -> list[str]:
    """Split on AND, ignoring any AND nested inside parentheses."""
    terms: list[str] = []
    depth = 0
    start = 0
    for match in re.finditer(r"[()]|\bAND\b", clause, re.IGNORECASE):
        token = match.group()
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            terms.append(clause[start : match.start()])
            start = match.end()
    terms.append(clause[start:])
    return [term.strip() for term in terms if term.strip()]


def _states_the_predicate(term: str) -> bool:
    return bool(_PREDICATE.match(_TRAILING_CLAUSE.split(term, maxsplit=1)[0].strip()))


def filters_by_consent(query: str) -> bool:
    """True when this query would exclude a revoked, missing or unknown consent."""
    normalized = " ".join(query.split())
    join = _CONSENT_JOIN.search(normalized)
    if not join:
        return False

    if _OUTER_CONSENT_JOIN.search(normalized):
        # An outer join keeps unmatched rows, so only the WHERE clause can drop them.
        parts = _WHERE.split(normalized, maxsplit=1)
        clause = parts[1] if len(parts) == 2 else ""
    else:
        # An inner join makes its own ON terms conjunctive with the WHERE terms.
        clause = _WHERE.sub(" AND ", normalized[join.end() :])

    return any(_states_the_predicate(term) for term in _top_level_terms(clause))

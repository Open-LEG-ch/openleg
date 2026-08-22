# SPDX-License-Identifier: AGPL-3.0-or-later
"""One home for the question every consent test actually has to answer.

CLAUDE.md states it: "A fake that accepts the *shape* of a query as proof of
its behaviour proves nothing." Matching substrings is not enough, and each of
these query shapes keeps every substring while filtering nobody:

- ``LEFT JOIN consents`` with the predicate in ``ON``: unmatched rows survive
- ``share_with_neighbors = TRUE OR share_with_neighbors IS NULL``: a disjunction
  is not a filter
- ``JOIN consents c ON TRUE``: a cross join matches any granted row, so one
  consenting neighbour lets every revoked one through
- ``-- share_with_neighbors = TRUE``: a commented predicate is not a predicate

``filters_by_consent`` answers whether Postgres would really drop a building
whose neighbour sharing is revoked, absent, or unknown. Doubles call it instead
of matching substrings, so a rewritten join changes what the double returns
rather than sliding past it.
"""

import re

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CONSENT_JOIN = re.compile(r"\bJOIN\s+consents\b", re.IGNORECASE)
_OUTER_CONSENT_JOIN = re.compile(
    r"\b(LEFT|RIGHT|FULL)\s+(OUTER\s+)?JOIN\s+consents\b", re.IGNORECASE
)
_ON = re.compile(r"\bON\b", re.IGNORECASE)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_CLAUSE_BOUNDARY = re.compile(
    r"\b(INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|FULL\s+JOIN|CROSS\s+JOIN|JOIN"
    r"|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|UNION|RETURNING)\b",
    re.IGNORECASE,
)
_TRAILING_CLAUSE = re.compile(
    r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|RETURNING|UNION)\b", re.IGNORECASE
)
_PREDICATE = re.compile(
    r"^\(*\s*\w*\.?share_with_neighbors\s*=\s*TRUE\s*\)*$", re.IGNORECASE
)
_BUILDING_BINDING = re.compile(
    r"\b(\w+)\.building_id\s*=\s*(\w+)\.building_id\b", re.IGNORECASE
)


def _normalize(query: str) -> str:
    """Drop comments first: a commented predicate must not become an active term."""
    without_comments = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", query))
    return " ".join(without_comments.split())


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


def _join_condition(normalized: str, join_end: int) -> str:
    """The ON clause of the consent join, up to the next clause keyword."""
    rest = normalized[join_end:]
    on = _ON.search(rest)
    if not on:
        return ""
    tail = rest[on.end() :]
    boundary = _CLAUSE_BOUNDARY.search(tail)
    return tail[: boundary.start()] if boundary else tail


def _binds_the_building(condition: str) -> bool:
    """The join must match one consent row per building, not any consent row."""
    match = _BUILDING_BINDING.search(condition)
    return bool(match) and match.group(1).lower() != match.group(2).lower()


def is_conjunctive_filter(query: str, predicate: re.Pattern) -> bool:
    """True when `predicate` matches one of the query's top-level AND terms.

    The same discipline as the consent gate, for any other predicate a double
    has to honour: a term wrapped in an OR is not a filter, and a commented one
    is not a term.
    """
    normalized = _normalize(query)
    parts = _WHERE.split(normalized, maxsplit=1)
    clause = parts[1] if len(parts) == 2 else normalized
    return any(
        predicate.match(_TRAILING_CLAUSE.split(term, maxsplit=1)[0].strip())
        for term in _top_level_terms(clause)
    )


def filters_by_consent(query: str) -> bool:
    """True when this query would exclude a revoked, missing or unknown consent."""
    normalized = _normalize(query)
    join = _CONSENT_JOIN.search(normalized)
    if not join:
        return False
    if not _binds_the_building(_join_condition(normalized, join.end())):
        return False

    if _OUTER_CONSENT_JOIN.search(normalized):
        # An outer join keeps unmatched rows, so only the WHERE clause can drop them.
        parts = _WHERE.split(normalized, maxsplit=1)
        clause = parts[1] if len(parts) == 2 else ""
    else:
        # An inner join makes its own ON terms conjunctive with the WHERE terms.
        clause = _WHERE.sub(" AND ", normalized[join.end() :])

    return any(_states_the_predicate(term) for term in _top_level_terms(clause))

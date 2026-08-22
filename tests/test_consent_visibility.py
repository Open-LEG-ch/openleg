# SPDX-License-Identifier: AGPL-3.0-or-later
"""The consent double's own contract. If this is wrong, every gate test lies."""

import pytest

from tests.consent_visibility import filters_by_consent

INNER_WITH_PREDICATE = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE b.verified = TRUE AND c.share_with_neighbors = TRUE
"""
BARE_JOIN_WITH_PREDICATE = """
    SELECT b.building_id FROM buildings b
    JOIN consents c ON b.building_id = c.building_id
    WHERE c.share_with_neighbors = TRUE
"""
INNER_WITHOUT_PREDICATE = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE b.verified = TRUE
"""
OUTER_WITH_PREDICATE_IN_ON = """
    SELECT b.building_id FROM buildings b
    LEFT JOIN consents c ON b.building_id = c.building_id
    AND c.share_with_neighbors = TRUE
    WHERE b.verified = TRUE
"""
OUTER_WITH_PREDICATE_IN_WHERE = """
    SELECT b.building_id FROM buildings b
    LEFT OUTER JOIN consents c ON b.building_id = c.building_id
    WHERE b.verified = TRUE AND c.share_with_neighbors = TRUE
"""
NO_CONSENT_JOIN = """
    SELECT b.building_id FROM buildings b WHERE b.verified = TRUE
"""
PREDICATE_INSIDE_AN_OR = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE b.verified = TRUE OR c.share_with_neighbors = TRUE
"""
PREDICATE_WIDENED_BY_IS_NULL = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    AND (c.share_with_neighbors = TRUE OR c.share_with_neighbors IS NULL)
    WHERE b.verified = TRUE
"""
PREDICATE_IN_AN_INNER_ON_CLAUSE = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    AND c.share_with_neighbors = TRUE
    WHERE b.verified = TRUE
"""
CROSS_JOIN_ON_TRUE = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON TRUE
    WHERE c.share_with_neighbors = TRUE
"""
JOIN_BOUND_TO_ANOTHER_TABLE = """
    SELECT b.building_id FROM buildings b
    JOIN referrals r ON b.building_id = r.referrer_id
    INNER JOIN consents c ON b.building_id = r.building_id
    WHERE c.share_with_neighbors = TRUE
"""
JOIN_WITHOUT_AN_ALIAS = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents ON b.building_id = consents.building_id
    WHERE consents.share_with_neighbors = TRUE
"""
PREDICATE_ON_ANOTHER_TABLE = """
    SELECT b.building_id FROM buildings b
    JOIN referrals r ON b.building_id = r.referrer_id
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE r.share_with_neighbors = TRUE
"""
PREDICATE_IN_A_LINE_COMMENT = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE b.verified = TRUE -- AND c.share_with_neighbors = TRUE
"""
PREDICATE_IN_A_BLOCK_COMMENT = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE b.verified = TRUE /* AND c.share_with_neighbors = TRUE */
"""
PREDICATE_WIDENED_BY_OR_TRUE = """
    SELECT b.building_id FROM buildings b
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE c.share_with_neighbors = TRUE OR TRUE
"""
PREDICATE_BEFORE_A_TRAILING_CLAUSE = """
    SELECT b.building_id, COUNT(r.id) FROM buildings b
    JOIN referrals r ON b.building_id = r.referrer_id
    INNER JOIN consents c ON b.building_id = c.building_id
    WHERE c.share_with_neighbors = TRUE
    GROUP BY b.building_id ORDER BY 2 DESC LIMIT %s
"""


@pytest.mark.parametrize(
    "query, filters",
    (
        pytest.param(INNER_WITH_PREDICATE, True, id="inner-join-with-predicate"),
        pytest.param(BARE_JOIN_WITH_PREDICATE, True, id="bare-join-is-inner"),
        pytest.param(INNER_WITHOUT_PREDICATE, False, id="inner-join-no-predicate"),
        pytest.param(
            OUTER_WITH_PREDICATE_IN_ON, False, id="outer-join-predicate-in-on"
        ),
        pytest.param(
            OUTER_WITH_PREDICATE_IN_WHERE, True, id="outer-join-predicate-in-where"
        ),
        pytest.param(NO_CONSENT_JOIN, False, id="no-consent-join"),
        pytest.param(PREDICATE_INSIDE_AN_OR, False, id="predicate-inside-an-or"),
        pytest.param(
            PREDICATE_WIDENED_BY_IS_NULL, False, id="predicate-widened-by-is-null"
        ),
        pytest.param(
            PREDICATE_IN_AN_INNER_ON_CLAUSE, True, id="inner-join-predicate-in-on"
        ),
        pytest.param(
            PREDICATE_BEFORE_A_TRAILING_CLAUSE, True, id="predicate-before-group-by"
        ),
        pytest.param(CROSS_JOIN_ON_TRUE, False, id="cross-join-on-true"),
        pytest.param(
            JOIN_BOUND_TO_ANOTHER_TABLE, False, id="join-bound-to-another-table"
        ),
        pytest.param(JOIN_WITHOUT_AN_ALIAS, True, id="join-without-an-alias"),
        pytest.param(
            PREDICATE_ON_ANOTHER_TABLE, False, id="predicate-on-another-table"
        ),
        pytest.param(
            PREDICATE_IN_A_LINE_COMMENT, False, id="predicate-in-a-line-comment"
        ),
        pytest.param(
            PREDICATE_IN_A_BLOCK_COMMENT, False, id="predicate-in-a-block-comment"
        ),
        pytest.param(
            PREDICATE_WIDENED_BY_OR_TRUE, False, id="predicate-widened-by-or-true"
        ),
    ),
)
def test_the_double_matches_what_postgres_would_do(query, filters):
    assert filters_by_consent(query) is filters

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
    ),
)
def test_the_double_matches_what_postgres_would_do(query, filters):
    assert filters_by_consent(query) is filters

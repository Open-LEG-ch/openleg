# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reviewed advisory identity and firmware interpretation at the domain API."""

import pytest

pytestmark = pytest.mark.unit


def entry(**changes):
    return {
        "reference": "SYNTHETIC-1",
        "publisher": "Example vendor",
        "source_url": "https://example.com/advisory",
        "source_revision": "1.0",
        "publication_date": "2025-08-27",
        "title": "Synthetic advisory",
        "summary": "Synthetic test evidence, not a published finding.",
        "vendor": "Example Solar AG",
        "vendor_aliases": ["Example Solar"],
        "models": [
            {
                "model": "Roof 1",
                "aliases": ["Roof-1"],
                "firmware": {
                    "scheme": "dotted-numeric",
                    "lower_inclusive": "1.0.0",
                    "upper_exclusive": "2.0.0",
                    "range_text": "From 1.0.0 to before 2.0.0",
                },
            }
        ],
        "remedy": {
            "text": "Ask the responsible installer.",
            "url": "https://example.com/remedy",
        },
        "status": "active",
        "change_note": "Initial source revision",
        **changes,
    }


def test_exact_reviewed_aliases_match_without_fuzzy_vendor_or_model_guessing():
    from advisories import match_advisory, validate_entry

    advisory = validate_entry(entry())
    result = match_advisory(
        {"manufacturer": "  EXAMPLE   SOLAR  ", "model": "roof-1", "firmware": "1.5.0"},
        advisory,
    )
    assert result["classification"] == "affected"
    assert result["reason"] == "firmware_within_range"
    assert result["matched_model"] == "Roof 1"
    mismatch = match_advisory(
        {"manufacturer": "Example", "model": "Roof 1", "firmware": "1.5.0"}, advisory
    )
    assert mismatch["classification"] == "no_identity_match"


@pytest.mark.parametrize(
    "firmware,classification",
    [
        ("1.0.0", "affected"),
        ("1.9.9", "affected"),
        ("0.9.9", "not_within"),
        ("2.0.0", "not_within"),
        (None, "possible"),
        ("1.5", "possible"),
        ("1.5.0-beta", "possible"),
        ("v1.5.0", "possible"),
        ("01.5.0", "possible"),
    ],
)
def test_numeric_range_boundaries_and_ambiguous_versions(firmware, classification):
    from advisories import match_advisory, validate_entry

    result = match_advisory(
        {"manufacturer": "Example Solar", "model": "Roof 1", "firmware": firmware},
        validate_entry(entry()),
    )
    assert result["classification"] == classification
    assert result["identity_matched"] is True


@pytest.mark.parametrize(
    "firmware,classification",
    [
        ("3.10.26.R", "affected"),
        ("3.9.99.R", "affected"),
        ("3.10.27.R", "not_within"),
        ("3.10.28.R", "not_within"),
        ("3.10.27.r", "possible"),
        ("3.10.27", "possible"),
        ("3.10.26.R-dev", "possible"),
        ("3.10.26.A", "possible"),
        (None, "possible"),
    ],
)
def test_sma_r_is_an_explicit_literal_suffix_version_profile(firmware, classification):
    from advisories import match_advisory, validate_entry

    payload = entry(
        models=[
            {
                "model": "Roof 1",
                "aliases": [],
                "firmware": {
                    "scheme": "sma-r",
                    "lower_inclusive": None,
                    "upper_exclusive": "3.10.27.R",
                    "range_text": "Versions before 3.10.27.R",
                },
            }
        ]
    )
    result = match_advisory(
        {"manufacturer": "Example Solar", "model": "Roof 1", "firmware": firmware},
        validate_entry(payload),
    )
    assert result["classification"] == classification


def test_missing_identity_or_an_unreviewed_vendor_variant_never_creates_a_notice():
    from advisories import assess_catalog, validate_entry

    published = {
        "advisory_id": "synthetic",
        "revision": 1,
        "entry": validate_entry(entry()),
        "review": {"reviewed_on": "2026-01-02"},
    }
    health = {"status": "current", "source_urls": ["https://example.com/advisory"]}
    for manufacturer, model in (
        (None, None),
        (None, "Roof 1"),
        ("Example Solar", None),
        ("Example", "Roof 1"),
        ("Example Solar", "Roof 1 Plus"),
    ):
        response = assess_catalog(
            {
                "id": "device",
                "manufacturer": manufacturer,
                "model": model,
                "firmware": "1.5.0",
            },
            [published],
            [],
            health,
        )
        assert response["notices"] == []


def test_review_cadence_expires_on_the_due_date_without_hiding_failure_history():
    from datetime import date

    from advisories import catalog_health, validate_policy

    policy = validate_policy(
        {
            "enabled": True,
            "review_owner": "Private reviewer",
            "cadence_days": 14,
            "coverage": "One source only",
            "source_urls": ["https://example.com/advisory"],
        }
    )
    reviews = [
        {"reviewed_on": "2026-10-01", "outcome": "success", "failure_code": None}
    ]
    assert (
        catalog_health(policy, reviews, today=date(2026, 10, 14))["status"] == "current"
    )
    assert (
        catalog_health(policy, reviews, today=date(2026, 10, 15))["status"] == "stale"
    )
    reviews.insert(
        0,
        {
            "reviewed_on": "2026-10-02",
            "outcome": "failure",
            "failure_code": "format_changed",
        },
    )
    failed = catalog_health(policy, reviews, today=date(2026, 10, 3))
    assert failed["status"] == "failed"
    assert failed["last_successful_review"] == "2026-10-01"


@pytest.mark.parametrize(
    "change",
    [
        {"source_url": "javascript:alert(1)"},
        {"source_url": "https://127.0x1/private"},
        {
            "remedy": {
                "text": "Unsafe source",
                "url": "https://example.com/fix?token=PRIVATE",
            }
        },
        {"publication_date": "9999-01-01"},
        {"publication_date": "2026-02-30"},
        {"vendor_aliases": ["Example Solar", "example   solar"]},
        {"models": []},
        {"models": [{"model": "Roof 1", "aliases": ["Roof 1"], "firmware": {}}]},
    ],
)
def test_untrusted_catalog_values_are_bounded_and_rejected_when_ambiguous(change):
    from advisories import AdvisoryError, validate_entry

    with pytest.raises(AdvisoryError):
        validate_entry(entry(**change))


def test_explicitly_unsupported_version_semantics_remain_unknown():
    from advisories import match_advisory, validate_entry

    payload = entry(
        models=[
            {
                "model": "Roof 1",
                "aliases": [],
                "firmware": {
                    "scheme": "unsupported",
                    "range_text": "Publisher uses an unsupported release train",
                },
            }
        ]
    )
    result = match_advisory(
        {"manufacturer": "Example Solar", "model": "Roof 1", "firmware": "1.5.0"},
        validate_entry(payload),
    )
    assert result["classification"] == "possible"
    assert result["reason"] == "unsupported_version"


def test_disabled_source_policy_cannot_enable_notices_after_a_successful_review():
    from datetime import date

    from advisories import catalog_health, validate_policy

    policy = validate_policy(
        {
            "enabled": False,
            "review_owner": "Private reviewer",
            "cadence_days": 14,
            "coverage": "One source",
            "source_urls": ["https://example.com/advisory"],
        }
    )
    health = catalog_health(
        policy,
        [{"reviewed_on": "2026-10-15", "outcome": "success", "failure_code": None}],
        today=date(2026, 10, 15),
    )
    assert health["status"] == "disabled"

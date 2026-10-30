# SPDX-License-Identifier: AGPL-3.0-or-later
"""Equipment evidence validation at the public domain boundary."""

import pytest

from equipment import EquipmentError, validate_record

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://www.bacs.admin.ch/path",
        "https://localhost/path",
        "https://127.0.0.1/path",
        "https://[::1]/path",
        "https://router.local/path",
        "https://example.com:bad/path",
        "https://user:password@example.com/path",
        "https://example.com/path?serial=PRIVATE",
        "https://example.com/path#PRIVATE",
        "https://www.bacs.admin.ch\\@evil.test/path",
        "https://device.internal/path",
        "https://127.1/path",
        "https://127.0x1/path",
        "https://0x7f.0x1/path",
        "https://router.home.arpa/path",
        "https://home.arpa/path",
        "https://docs..example.com/path",
    ],
)
def test_evidence_links_only_accept_public_https_document_urls(url):
    with pytest.raises(EquipmentError):
        validate_record(
            {
                "evidence": {
                    "kind": "owner_report",
                    "reviewed_on": "2026-01-02",
                    "source_url": url,
                }
            }
        )


def test_evidence_dates_reject_falsy_non_dates():
    for value in (False, [], {}, 0):
        with pytest.raises(EquipmentError):
            validate_record({"evidence": {"kind": "unknown", "reviewed_on": value}})

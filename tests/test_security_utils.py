# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct contracts for the input validators.

Every route test mocks `security_utils` wholesale to exercise the route's own
branching, so a bug inside a validator could not be caught anywhere. These call
the real functions.
"""

from types import SimpleNamespace

import pytest

import security_utils

MAX_REQUEST_BYTES = 1024 * 1024

# ---------------------------------------------------------------------------
# sanitize_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    (
        pytest.param("", "", id="empty"),
        pytest.param(None, "", id="none"),
        pytest.param("  Badstrasse 1  ", "Badstrasse 1", id="trimmed"),
        pytest.param("<script>alert(1)</script>x", "alert(1)x", id="script-tags-go"),
        pytest.param("<img src=x onerror=alert(1)>", "", id="attribute-payload-goes"),
        pytest.param("<b>bold</b>", "bold", id="markup-stripped"),
        pytest.param("line\nbreak\tkept", "line\nbreak\tkept", id="whitespace-kept"),
        pytest.param("null\x00byte", "nullbyte", id="control-character-dropped"),
    ),
)
def test_sanitize_string(raw, expected):
    assert security_utils.sanitize_string(raw) == expected


def test_sanitize_string_never_returns_markup():
    """Tags go; the text between them stays as text, which Jinja then escapes.

    Pinned because the two halves are easy to confuse: the sanitiser is not an
    XSS filter for script bodies, it is a markup stripper.
    """
    for raw in (
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<b>bold</b>",
        "<a href='javascript:alert(1)'>x</a>",
    ):
        cleaned = security_utils.sanitize_string(raw)
        assert "<" not in cleaned and ">" not in cleaned


def test_sanitize_string_truncates_to_the_requested_length():
    assert security_utils.sanitize_string("a" * 40, max_length=10) == "a" * 10


# ---------------------------------------------------------------------------
# validate_phone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, normalized",
    (
        pytest.param("+41791234567", "+41791234567", id="already-normalized"),
        pytest.param("079 123 45 67", "+41791234567", id="national-with-spaces"),
        pytest.param("0041791234567", "+41791234567", id="double-zero-prefix"),
        pytest.param("079-123-45-67", "+41791234567", id="hyphenated"),
    ),
)
def test_validate_phone_normalizes_swiss_numbers(raw, normalized):
    is_valid, value, error = security_utils.validate_phone(raw)

    assert (is_valid, value, error) == (True, normalized, None)


def test_validate_phone_treats_an_absent_number_as_optional():
    assert security_utils.validate_phone("") == (True, None, None)


@pytest.mark.parametrize(
    "raw",
    ("07912345", "+4179123456789", "+49791234567", "not a number", "+41 79 123 45 6"),
)
def test_validate_phone_rejects_anything_that_is_not_a_swiss_number(raw):
    is_valid, value, error = security_utils.validate_phone(raw)

    assert is_valid is False
    assert value is None
    assert error


# ---------------------------------------------------------------------------
# validate_coordinates
# ---------------------------------------------------------------------------


def test_validate_coordinates_accepts_a_swiss_point():
    assert security_utils.validate_coordinates(47.4736, 8.3060) == (True, None)


def test_validate_coordinates_accepts_numeric_strings():
    assert security_utils.validate_coordinates("47.4736", "8.3060") == (True, None)


@pytest.mark.parametrize(
    "lat, lon",
    (
        pytest.param(None, 8.3, id="missing-latitude"),
        pytest.param("north", 8.3, id="unparseable"),
        pytest.param(52.5, 13.4, id="berlin-out-of-bounds"),
        pytest.param(52.5, 8.0, id="north-of-switzerland"),
        pytest.param(43.0, 8.0, id="south-of-switzerland"),
        pytest.param(47.4, 2.3, id="paris-out-of-bounds"),
        pytest.param(0, 0, id="null-island"),
    ),
)
def test_validate_coordinates_rejects_points_outside_switzerland(lat, lon):
    is_valid, error = security_utils.validate_coordinates(lat, lon)

    assert is_valid is False
    assert error


# ---------------------------------------------------------------------------
# validate_building_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ("building-1", "BFS_4021_a", "12345"))
def test_validate_building_id_accepts_url_safe_identifiers(raw):
    assert security_utils.validate_building_id(raw) == (True, None)


@pytest.mark.parametrize(
    "raw",
    (
        pytest.param("", id="empty"),
        pytest.param(None, id="none"),
        pytest.param("building 1", id="space"),
        pytest.param("building/1", id="slash"),
        pytest.param("../etc/passwd", id="traversal"),
        pytest.param("b" * 101, id="too-long"),
    ),
)
def test_validate_building_id_rejects_anything_else(raw):
    is_valid, error = security_utils.validate_building_id(raw)

    assert is_valid is False
    assert error


# ---------------------------------------------------------------------------
# validate_token and validate_uuid
# ---------------------------------------------------------------------------


def test_validate_token_accepts_a_uuid_in_either_case():
    token = security_utils.generate_uuid()

    assert security_utils.validate_token(token) == (True, None)
    assert security_utils.validate_token(token.upper()) == (True, None)


@pytest.mark.parametrize(
    "raw",
    (
        pytest.param("", id="empty"),
        pytest.param(None, id="none"),
        pytest.param("not-a-uuid", id="not-a-uuid"),
        pytest.param("12345678-1234-1234-1234-12345678901", id="one-digit-short"),
        pytest.param("12345678-1234-1234-1234-1234567890123", id="one-digit-long"),
        pytest.param("g2345678-1234-1234-1234-123456789012", id="non-hex"),
    ),
)
def test_validate_token_rejects_anything_that_is_not_a_uuid(raw):
    is_valid, error = security_utils.validate_token(raw)

    assert is_valid is False
    assert error


def test_validate_uuid_normalizes_and_raises_on_a_bad_token():
    token = security_utils.generate_uuid().upper()

    assert security_utils.validate_uuid(token) == token.lower()

    with pytest.raises(ValueError):
        security_utils.validate_uuid("not-a-uuid")


# ---------------------------------------------------------------------------
# check_request_size
# ---------------------------------------------------------------------------


def test_check_request_size_allows_an_unknown_length():
    request = SimpleNamespace(content_length=None)

    assert security_utils.check_request_size(request) == (True, None)


def test_check_request_size_allows_the_limit_itself():
    request = SimpleNamespace(content_length=MAX_REQUEST_BYTES)

    assert security_utils.check_request_size(request) == (True, None)


def test_check_request_size_rejects_one_byte_over():
    request = SimpleNamespace(content_length=MAX_REQUEST_BYTES + 1)
    is_valid, error = security_utils.check_request_size(request)

    assert is_valid is False
    assert error == "Anfrage ist zu gross"


# ---------------------------------------------------------------------------
# Nothing unreachable stays behind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ("is_safe_redirect_url", "sanitize_json_output", "rate_limit_key_func")
)
def test_the_uncalled_helpers_are_gone(name):
    """An unused security control reads as protection that is not in the path.

    None of these three had a caller anywhere in the repository. The project
    precedent is to delete rather than gate what nothing consumes.
    """
    assert not hasattr(security_utils, name)

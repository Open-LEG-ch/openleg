# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime release metadata shared by health and the public footer."""

from unittest.mock import patch

import pytest
from flask import render_template

from app_config import build_config
from tests.test_i18n import app_module, isolated_environment  # noqa: F401

TUPLE = {"tag": "v1.2.3", "source_sha": "a" * 40, "image_digest": "sha256:" + "b" * 64}
ENV = {
    "OPENLEG_RELEASE_TAG": TUPLE["tag"],
    "OPENLEG_RELEASE_SHA": TUPLE["source_sha"],
    "OPENLEG_RELEASE_IMAGE_DIGEST": TUPLE["image_digest"],
}


def test_config_exposes_validated_identity():
    identity = build_config(ENV)["RELEASE_IDENTITY"]
    assert identity.as_dict() == TUPLE
    assert (
        identity.source_url
        == "https://github.com/Open-LEG-ch/openleg/tree/" + TUPLE["source_sha"]
    )
    assert (
        identity.release_url
        == "https://github.com/Open-LEG-ch/openleg/releases/tag/v1.2.3"
    )
    assert build_config({})["RELEASE_IDENTITY"] is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("OPENLEG_RELEASE_TAG", "<script>alert(1)</script>"),
        ("OPENLEG_RELEASE_TAG", "javascript:alert(1)"),
        ("OPENLEG_RELEASE_SHA", "abc"),
        ("OPENLEG_RELEASE_IMAGE_DIGEST", "sha256:bad"),
        ("OPENLEG_RELEASE_TAG", ""),
    ],
)
def test_invalid_or_partial_identity_is_refused(key, value):
    with pytest.raises(ValueError, match="release identity"):
        build_config({**ENV, key: value})


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("supplied", [False, True])
def test_health_and_footer_share_runtime_identity(app_module, enabled, supplied):  # noqa: F811
    config = {
        **(ENV if supplied else {}),
        "SITE_RELEASE_ENABLED": enabled,
        "TESTING": True,
    }
    application = app_module.create_app(config, check_database=False)
    with patch("health.db.get_connection"):
        response = application.test_client().get("/health")
    assert response.status_code == 200
    assert response.json["release"] == (TUPLE if supplied else None)
    with application.test_request_context("/"):
        html = render_template(
            "partials/site_footer.html", contact_email="test@example.invalid"
        )
    if supplied:
        assert (
            'href="https://github.com/Open-LEG-ch/openleg/tree/'
            + TUPLE["source_sha"]
            + '"'
            in html
        )
        assert (
            'href="https://github.com/Open-LEG-ch/openleg/releases/tag/v1.2.3"' in html
        )
        assert TUPLE["image_digest"] in html
    else:
        assert "/releases/tag/" not in html
        assert "sha256:" not in html


def test_degraded_health_keeps_identity_and_status_code(app_module):  # noqa: F811
    application = app_module.create_app(ENV, check_database=False)
    with patch("health.db.get_connection", side_effect=RuntimeError("offline")):
        response = application.test_client().get("/health")
    assert response.status_code == 503
    assert response.json["status"] == "degraded"
    assert response.json["release"] == TUPLE

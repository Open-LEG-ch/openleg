"""Release health identity required by the product deployment contract."""

from unittest.mock import patch

import pytest
from flask import Flask

from app_config import build_config
from health import health_bp

IDENTITY = {
    "tag": "v1.2.3",
    "source_sha": "a" * 40,
    "image_digest": "sha256:" + "b" * 64,
}
ENV = {
    "OPENLEG_RELEASE_TAG": IDENTITY["tag"],
    "OPENLEG_RELEASE_SHA": IDENTITY["source_sha"],
    "OPENLEG_RELEASE_IMAGE_DIGEST": IDENTITY["image_digest"],
}


def test_health_reports_configured_release():
    application = Flask(__name__)
    application.config.update(build_config(ENV))
    application.register_blueprint(health_bp)
    with patch("health.db.get_connection"), patch("cache._get_redis"):
        response = application.test_client().get("/health")
    assert response.status_code == 200
    assert response.json["release"] == IDENTITY


@pytest.mark.parametrize(
    "key,value",
    [
        ("OPENLEG_RELEASE_TAG", ""),
        ("OPENLEG_RELEASE_TAG", "v1.2.3<script>"),
        ("OPENLEG_RELEASE_SHA", "abc"),
        ("OPENLEG_RELEASE_IMAGE_DIGEST", "sha256:bad"),
    ],
)
def test_partial_or_malformed_identity_is_rejected(key, value):
    with pytest.raises(ValueError, match="release identity"):
        build_config({**ENV, key: value})


@pytest.mark.parametrize("configured", [False, True])
def test_degraded_health_preserves_status_and_optional_identity(configured):
    application = Flask(__name__)
    application.config.update(build_config(ENV if configured else {}))
    application.register_blueprint(health_bp)
    with (
        patch("health.db.get_connection", side_effect=RuntimeError("offline")),
        patch("cache._get_redis"),
    ):
        response = application.test_client().get("/health")
    assert response.status_code == 503
    assert response.json["status"] == "degraded"
    assert response.json["release"] == (IDENTITY if configured else None)
    assert application.test_client().get("/livez").status_code == 200

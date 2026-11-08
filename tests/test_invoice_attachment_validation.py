# SPDX-License-Identifier: AGPL-3.0-or-later
"""A claimed PDF content type does not authorize arbitrary stored bytes."""

import io
from unittest.mock import MagicMock

import pytest

from tests import security_review_fixtures
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module


@pytest.mark.parametrize(
    "filename,content,mimetype,status",
    [
        ("question.pdf", b"<html>not a PDF</html>", "application/pdf", 400),
        ("question.html", b"%PDF-1.7\n", "application/pdf", 400),
        ("question.pdf", b"%PDF-1.7\n", "text/plain", 400),
        ("question.pdf", b"%PDF-" + b"x" * (2 * 1024 * 1024), "application/pdf", 400),
        ("question.PDF", b"%PDF-1.7\n", "application/pdf", 302),
    ],
)
def test_invoice_question_checks_extension_content_and_size(
    app_module, monkeypatch, filename, content, mimetype, status
):
    save = MagicMock(return_value=7)
    monkeypatch.setattr(app_module.db, "open_invoice_query", save)
    client = app_module.web.test_client()
    _set_session(client)
    response = client.post(
        "/dashboard/invoices/42/queries",
        data={
            "csrf_token": "csrf-secret",
            "category": "other",
            "message": "A question",
            "attachment": (io.BytesIO(content), filename, mimetype),
        },
    )
    assert response.status_code == status
    if status == 400:
        save.assert_not_called()
    else:
        assert save.call_args.args[-1] == content

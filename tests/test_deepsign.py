"""TDD tests for deepsign_integration.py - AES e-signature via DeepSign API."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_requests():
    with patch('deepsign_integration.requests') as m:
        yield m


class TestDocumentUpload:
    """Test uploading PDF to DeepSign."""

    def test_upload_returns_document_id(self, mock_requests):
        from deepsign_integration import upload_document
        mock_requests.post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": "doc_abc123", "status": "uploaded"}
        )
        doc_id = upload_document(
            pdf_bytes=b'%PDF-fake',
            filename="gv_leg_test.pdf",
            title="Gemeinschaftsvereinbarung LEG Test",
        )
        assert doc_id == "doc_abc123"

    def test_upload_failure_raises(self, mock_requests):
        from deepsign_integration import upload_document
        mock_requests.post.return_value = MagicMock(
            status_code=400,
            json=lambda: {"error": "invalid"},
            text="Bad Request"
        )
        with pytest.raises(Exception, match="DeepSign"):
            upload_document(b'bad', "test.pdf", "Test")


class TestSignatureRequest:
    """Test requesting AES signatures from signers."""

    def test_request_signatures(self, mock_requests):
        from deepsign_integration import request_signatures
        mock_requests.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "sig_req_123", "status": "pending"}
        )
        result = request_signatures(
            document_id="doc_abc123",
            signers=[
                {"name": "Max Muster", "email": "max@example.com"},
                {"name": "Anna B", "email": "anna@example.com"},
            ],
        )
        assert result["id"] == "sig_req_123"


class TestWebhookCallback:
    """Test processing DeepSign webhook callbacks."""

    def test_completed_signature(self):
        from deepsign_integration import handle_webhook
        payload = {
            "event": "document.signed",
            "document_id": "doc_abc123",
            "status": "completed",
        }
        result = handle_webhook(payload)
        assert result["action"] == "signature_completed"
        assert result["document_id"] == "doc_abc123"

    def test_rejected_signature(self):
        from deepsign_integration import handle_webhook
        payload = {
            "event": "document.rejected",
            "document_id": "doc_abc123",
            "status": "rejected",
        }
        result = handle_webhook(payload)
        assert result["action"] == "signature_rejected"


class TestStatusCheck:
    """Test checking document signing status."""

    def test_get_status(self, mock_requests):
        from deepsign_integration import get_signing_status
        mock_requests.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "doc_abc123", "status": "completed", "signed_pdf_url": "https://..."}
        )
        status = get_signing_status("doc_abc123")
        assert status["status"] == "completed"

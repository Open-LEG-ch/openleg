"""DeepSign AES e-signature integration for LEG formation documents.

Requires DEEPSIGN_API_KEY and DEEPSIGN_API_URL env vars.
API docs: https://docs.deepsign.ch
"""
import os
import requests

API_URL = os.environ.get("DEEPSIGN_API_URL", "https://api.deepsign.ch/v1")
API_KEY = os.environ.get("DEEPSIGN_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("DEEPSIGN_WEBHOOK_SECRET", "")


def _headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
    }


def upload_document(pdf_bytes, filename, title):
    """Upload PDF to DeepSign for signing.

    Returns: document ID string

    Raises: Exception on upload failure
    """
    resp = requests.post(
        f"{API_URL}/documents",
        headers=_headers(),
        files={"file": (filename, pdf_bytes, "application/pdf")},
        data={"title": title},
    )
    if resp.status_code not in (200, 201):
        raise Exception(f"DeepSign upload failed ({resp.status_code}): {resp.text}")
    return resp.json()["id"]


def request_signatures(document_id, signers):
    """Request AES signatures from a list of signers.

    Args:
        document_id: DeepSign document ID
        signers: list of {"name": ..., "email": ...}

    Returns: signature request dict with id and status
    """
    resp = requests.post(
        f"{API_URL}/documents/{document_id}/signatures",
        headers=_headers(),
        json={"signers": signers, "signature_type": "AES"},
    )
    if resp.status_code not in (200, 201):
        raise Exception(f"DeepSign signature request failed ({resp.status_code}): {resp.text}")
    return resp.json()


def handle_webhook(payload):
    """Process DeepSign webhook callback.

    Returns: dict with action and document_id
    """
    event = payload.get("event", "")
    doc_id = payload.get("document_id", "")

    if event == "document.signed":
        _update_formation_status(doc_id, "signed")
        return {"action": "signature_completed", "document_id": doc_id}
    elif event == "document.rejected":
        _update_formation_status(doc_id, "rejected")
        return {"action": "signature_rejected", "document_id": doc_id}
    else:
        return {"action": "unknown", "document_id": doc_id, "event": event}


def get_signing_status(document_id):
    """Check current signing status of a document.

    Returns: dict with id, status, and optional signed_pdf_url
    """
    resp = requests.get(
        f"{API_URL}/documents/{document_id}",
        headers=_headers(),
    )
    if resp.status_code != 200:
        raise Exception(f"DeepSign status check failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _update_formation_status(document_id, status):
    """Update formation step based on signing event."""
    try:
        import database
        database.update_document_signing_status(document_id, status)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[DEEPSIGN] update_formation_status failed: {e}")

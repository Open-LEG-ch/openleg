# SPDX-License-Identifier: AGPL-3.0-or-later
"""Original registrations stay locked until billing persistence commits."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

import pytest

import billing_approval
import billing_runner
from store import billing
from tests import test_billing_precision_postgres as precision

interest_database = precision.interest_database
calculated_client = precision.calculated_client
assignment_request = precision.assignment_request
configured_e66 = precision.configured_e66
rounded_delivery = precision.rounded_delivery
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("phase", ["persistence", "issuance"])
def test_deletion_waits_for_the_frozen_registration_transaction(
    rounded_delivery, monkeypatch, phase
):
    """Deletion waits for persistence or issuance to release its registration locks."""
    client, db, payload, source = rounded_delivery
    assert precision.e66.post_e66(client, source).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    cid = payload["community_id"]
    prepared = None
    if phase == "issuance":
        prepared = billing_runner.run_billing_period(
            cid, start, end, prepared_by="member"
        )
    token = str(uuid4())
    assert db.save_token(token, "consumer-b", "unsubscribe")
    locked, release, deleting = threading.Event(), threading.Event(), threading.Event()
    original_lock = billing.lock_original_registrations
    original_connection = db.get_connection
    deletion_pid = []

    def hold_registration(*args):
        """Pause after the real registration locks have been acquired."""
        original_lock(*args)
        locked.set()
        assert release.wait(15), "billing transaction was not released"

    @contextmanager
    def observe_connection():
        """Expose the deletion backend so the test can prove it is blocked."""
        with original_connection() as connection:
            if threading.current_thread().name.startswith("registration-delete"):
                deletion_pid.append(connection.get_backend_pid())
                deleting.set()
            yield connection

    monkeypatch.setattr(billing, "lock_original_registrations", hold_registration)
    monkeypatch.setattr(db, "get_connection", observe_connection)

    def bill():
        """Run the selected persistence or issuance phase on its own connection."""
        if phase == "persistence":
            return billing_runner.run_billing_period(
                cid, start, end, prepared_by="member"
            )
        return db.approve_billing_period(
            prepared["period_id"], cid, approver_id="producer"
        )

    with (
        ThreadPoolExecutor(max_workers=1) as reader,
        ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="registration-delete"
        ) as writer,
    ):
        billing_result = reader.submit(bill)
        try:
            assert locked.wait(5), "billing did not acquire registration locks"
            deletion = writer.submit(db.confirm_profile_deletion, token)
            assert deleting.wait(5), "deletion did not connect"
            deadline = time.monotonic() + 5
            with original_connection() as connection, connection.cursor() as cursor:
                while True:
                    cursor.execute(
                        "SELECT pg_blocking_pids(%s) AS blockers", (deletion_pid[0],)
                    )
                    if cursor.fetchone()["blockers"]:
                        break
                    assert time.monotonic() < deadline, (
                        "deletion bypassed the registration lock"
                    )
                    time.sleep(0.01)
            assert not deletion.done()
        finally:
            release.set()
        result = billing_result.result(timeout=10)
        assert deletion.result(timeout=10) is True
    assert db.get_building("consumer-b") is None
    if phase == "persistence":
        with pytest.raises(billing_approval.BillingApprovalError):
            db.approve_billing_period(result["period_id"], cid, approver_id="producer")
    else:
        own = next(
            invoice for invoice in result if invoice["participant_id"] == "consumer-b"
        )
        assert own["provenance_snapshot"]["participant_registration_id"]
        assert db.get_invoice_for_participant(own["id"], "consumer-b") is None

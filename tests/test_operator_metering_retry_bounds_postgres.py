# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retry cooldown and concurrent claims through HTTP and real PostgreSQL."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Barrier, Event
from unittest.mock import patch

import pytest

from tests import test_interest_postgres, test_operator_metering_retry_bounds

interest_database = test_interest_postgres.interest_database
retry_app = test_operator_metering_retry_bounds.retry_app
retry = test_operator_metering_retry_bounds.retry
pytestmark = pytest.mark.integration


@pytest.fixture
def job(interest_database):
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('retry-community','Fictional LEG')"
        )
        cur.execute(
            """INSERT INTO buildings(building_id,email,address,lat,lon,city_id)
               VALUES ('retry-admin','test@example.ch','Testweg 1',47,8,'retry-tenant')"""
        )
        cur.execute(
            """UPDATE communities SET admin_building_id='retry-admin'
               WHERE community_id='retry-community'"""
        )
        cur.execute(
            "INSERT INTO white_label_configs(territory) VALUES ('retry-tenant')"
        )
        cur.execute(
            """INSERT INTO sdat_ingestion_schedules(territory,max_attempts,retry_seconds)
               VALUES ('retry-tenant',5,300)"""
        )
        cur.execute(
            """INSERT INTO sdat_ingestion_runs(territory,started_at,finished_at,status,attempts)
               VALUES ('retry-tenant',NOW(),NOW(),'failure',5) RETURNING id"""
        )
        return cur.fetchone()["id"]


@pytest.mark.parametrize("status", ["success", "failure"])
def test_completed_retry_cools_down_new_keys_but_replays_first_key(
    interest_database, retry_app, job, status
):
    result = {"status": status, "territory": "retry-tenant", "attempts": 1}
    with patch("operator_api.sdat_ingestion.run", return_value=result) as run:
        first = retry(retry_app, "first", job)
        assert first.status_code == 200
        second = retry(retry_app, "second", job)
        assert second.status_code == 429
        assert second.json == {
            "code": "rate_limited",
            "error": "Rate limit exceeded",
            "retryable": True,
            "schema_version": "operator-api/1",
        }
        assert 895 <= int(second.headers["Retry-After"]) <= 900
        assert second.headers["Cache-Control"] == "no-store"
        replay = retry(retry_app, "first", job)
        assert replay.status_code == 200
        assert replay.json == {**first.json, "replayed": True}
        assert run.call_count == 1

        with interest_database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE operator_action_idempotency
                   SET completed_at=NOW()-INTERVAL '14 minutes 30 seconds'
                   WHERE community_id='retry-community' AND idempotency_key='first'"""
            )
        still_blocked = retry(retry_app, "second", job)
        assert still_blocked.status_code == 429
        assert 25 <= int(still_blocked.headers["Retry-After"]) <= 30
        assert run.call_count == 1

        with interest_database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE operator_action_idempotency
                   SET completed_at=NOW()-INTERVAL '16 minutes'
                   WHERE community_id='retry-community' AND idempotency_key='first'"""
            )
        allowed = retry(retry_app, "second", job)
        assert allowed.status_code == 200
        assert run.call_count == 2


def test_different_keys_cannot_run_concurrently(interest_database, retry_app, job):
    start = Barrier(2)
    entered = Event()
    finish = Event()

    # Widen the first-claim race at the actual database insert.
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE FUNCTION delay_retry_claim() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END $$;
            CREATE TRIGGER delay_retry_claim BEFORE INSERT ON operator_action_idempotency
            FOR EACH ROW EXECUTE FUNCTION delay_retry_claim();
        """)

    def ingest(*args, **kwargs):
        entered.set()
        assert finish.wait(10)
        return {"status": "failure", "territory": "retry-tenant", "attempts": 1}

    def request(key):
        start.wait(timeout=10)
        return retry(retry_app, key, job)

    with patch("operator_api.sdat_ingestion.run", side_effect=ingest) as run:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(request, key) for key in ("one", "two")]
            try:
                assert entered.wait(10)
                # One request must finish while ingestion remains in flight.
                done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
                assert len(done) == 1
                blocked = next(iter(done)).result()
                assert blocked.status_code == 409
                assert blocked.json["error"] == "Retry already in progress"
            finally:
                finish.set()
            responses = [future.result(timeout=10) for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert run.call_count == 1


def test_an_abandoned_claim_expires_so_the_job_can_be_retried(
    interest_database, retry_app, job
):
    """A worker killed mid-run never releases its claim; it must not block forever."""
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO operator_action_idempotency
                      (community_id,action,idempotency_key,request_hash,response,created_at)
               VALUES ('retry-community',%s,'killed','x',NULL,
                       NOW()-INTERVAL '16 minutes')""",
            (f"metering.retry:{job}",),
        )
    result = {"status": "success", "territory": "retry-tenant", "attempts": 1}
    with patch("operator_api.sdat_ingestion.run", return_value=result) as run:
        fresh = retry(retry_app, "after-crash", job)
    assert fresh.status_code == 200
    assert run.call_count == 1


def test_a_recent_claim_still_blocks_other_keys(interest_database, retry_app, job):
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO operator_action_idempotency
                      (community_id,action,idempotency_key,request_hash,response,created_at)
               VALUES ('retry-community',%s,'running','x',NULL,
                       NOW()-INTERVAL '14 minutes')""",
            (f"metering.retry:{job}",),
        )
    with patch("operator_api.sdat_ingestion.run") as run:
        blocked = retry(retry_app, "other", job)
    assert blocked.status_code == 409
    assert not run.called

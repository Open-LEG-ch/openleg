# SPDX-License-Identifier: AGPL-3.0-or-later
"""Immutable advisory revisions/publications and private equipment evidence."""

import uuid

from psycopg2.extras import Json

import advisories
from advisories import AdvisoryError


def _get_connection():
    import database

    return database.get_connection()


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS advisory_catalog (
            advisory_id UUID PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS advisory_revisions (
            advisory_id UUID NOT NULL REFERENCES advisory_catalog(advisory_id),
            revision INTEGER NOT NULL,
            content JSONB NOT NULL,
            digest CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(advisory_id,revision)
        );
        CREATE TABLE IF NOT EXISTS advisory_publications (
            advisory_id UUID NOT NULL,
            revision INTEGER NOT NULL,
            reviewer_id VARCHAR(120) NOT NULL,
            reviewed_on DATE NOT NULL,
            published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(advisory_id,revision),
            FOREIGN KEY(advisory_id,revision)
                REFERENCES advisory_revisions(advisory_id,revision)
        );
        CREATE TABLE IF NOT EXISTS advisory_review_policies (
            revision SERIAL PRIMARY KEY,
            policy JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS advisory_source_reviews (
            id BIGSERIAL PRIMARY KEY,
            policy_revision INTEGER NOT NULL REFERENCES advisory_review_policies(revision),
            outcome VARCHAR(16) NOT NULL,
            reviewed_on DATE NOT NULL,
            review_owner VARCHAR(120) NOT NULL,
            failure_code VARCHAR(40),
            source_urls JSONB NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS equipment_advisory_actions (
            equipment_id UUID NOT NULL REFERENCES equipment_records(id) ON DELETE CASCADE,
            advisory_id UUID NOT NULL,
            revision INTEGER NOT NULL,
            context_digest CHAR(64) NOT NULL,
            device_context JSONB NOT NULL,
            action JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(equipment_id,advisory_id,revision,context_digest),
            FOREIGN KEY(advisory_id,revision) REFERENCES advisory_publications(advisory_id,revision)
        );
    """)


def _revision(row, latest_published=None):
    published = row["reviewer_id"] is not None
    return {
        "advisory_id": str(row["advisory_id"]),
        "revision": row["revision"],
        "entry": row["content"],
        "digest": row["digest"],
        "state": advisories.revision_state(
            row["content"]["status"], published, row["revision"], latest_published or 0
        ),
        "review": {
            "reviewer_id": row["reviewer_id"],
            "reviewed_on": row["reviewed_on"].isoformat(),
        }
        if published
        else None,
    }


def create_advisory(payload):
    content = advisories.validate_entry(payload)
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute("LOCK TABLE advisory_catalog IN EXCLUSIVE MODE")
        cur.execute("SELECT COUNT(*) AS count FROM advisory_catalog")
        if cur.fetchone()["count"] >= advisories.MAX_CATALOG:
            raise AdvisoryError("catalog_limit_reached", 409)
        advisory_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO advisory_catalog(advisory_id) VALUES(%s)", (advisory_id,)
        )
        digest = advisories.content_digest(content)
        cur.execute(
            "INSERT INTO advisory_revisions(advisory_id,revision,content,digest) VALUES(%s,1,%s,%s)",
            (advisory_id, Json(content), digest),
        )
        return {
            "advisory_id": advisory_id,
            "revision": 1,
            "entry": content,
            "digest": digest,
            "state": "draft",
            "review": None,
        }


def list_advisory_catalog():
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON(r.advisory_id) r.*,p.reviewer_id,p.reviewed_on
            FROM advisory_revisions r LEFT JOIN advisory_publications p
              ON p.advisory_id=r.advisory_id AND p.revision=r.revision
            ORDER BY r.advisory_id,r.revision DESC
        """)
        return [_revision(row) for row in cur.fetchall()]


def _lock_catalog(cur, advisory_id):
    cur.execute(
        "SELECT advisory_id FROM advisory_catalog WHERE advisory_id=%s FOR UPDATE",
        (advisory_id,),
    )
    if not cur.fetchone():
        raise AdvisoryError("advisory_not_found", 404)


def get_advisory_history(advisory_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.*,p.reviewer_id,p.reviewed_on FROM advisory_revisions r
            LEFT JOIN advisory_publications p ON p.advisory_id=r.advisory_id AND p.revision=r.revision
            WHERE r.advisory_id=%s ORDER BY r.revision
        """,
            (advisory_id,),
        )
        rows = cur.fetchall()
        if not rows:
            raise AdvisoryError("advisory_not_found", 404)
        latest = max((row["revision"] for row in rows if row["reviewer_id"]), default=0)
        return [_revision(row, latest) for row in rows]


def append_advisory_revision(advisory_id, payload):
    content = advisories.validate_entry(payload)
    with _get_connection() as conn, conn.cursor() as cur:
        _lock_catalog(cur, advisory_id)
        cur.execute(
            "SELECT MAX(revision) AS latest FROM advisory_revisions WHERE advisory_id=%s",
            (advisory_id,),
        )
        revision = cur.fetchone()["latest"] + 1
        if revision > advisories.MAX_REVISIONS:
            raise AdvisoryError("revision_limit_reached", 409)
        digest = advisories.content_digest(content)
        cur.execute(
            "INSERT INTO advisory_revisions(advisory_id,revision,content,digest) VALUES(%s,%s,%s,%s)",
            (advisory_id, revision, Json(content), digest),
        )
        return {
            "advisory_id": advisory_id,
            "revision": revision,
            "entry": content,
            "digest": digest,
            "state": "draft",
            "review": None,
        }


def publish_advisory_revision(advisory_id, revision, payload):
    with _get_connection() as conn, conn.cursor() as cur:
        _lock_catalog(cur, advisory_id)
        cur.execute(
            "SELECT * FROM advisory_revisions WHERE advisory_id=%s ORDER BY revision DESC LIMIT 1",
            (advisory_id,),
        )
        row = cur.fetchone()
        if row["revision"] != revision:
            raise AdvisoryError("revision_superseded", 409)
        cur.execute(
            "SELECT 1 FROM advisory_publications WHERE advisory_id=%s AND revision=%s",
            (advisory_id, revision),
        )
        if cur.fetchone():
            raise AdvisoryError("already_published", 409)
        review = advisories.validate_publication(payload, row["content"], row["digest"])
        cur.execute(
            "INSERT INTO advisory_publications(advisory_id,revision,reviewer_id,reviewed_on) VALUES(%s,%s,%s,%s)",
            (advisory_id, revision, review["reviewer_id"], review["reviewed_on"]),
        )
        return {
            "advisory_id": advisory_id,
            "revision": revision,
            "entry": row["content"],
            "digest": row["digest"],
            "state": row["content"]["status"],
            "review": review,
        }


def _policy(cur):
    cur.execute(
        "SELECT revision,policy FROM advisory_review_policies ORDER BY revision DESC LIMIT 1"
    )
    row = cur.fetchone()
    return {"revision": row["revision"], **row["policy"]} if row else None


def _health(cur, policy):
    if not policy:
        return advisories.catalog_health(None, [])
    cur.execute(
        """
        SELECT reviewed_on,outcome,failure_code FROM advisory_source_reviews
        WHERE id IN (
            (SELECT MAX(id) FROM advisory_source_reviews WHERE policy_revision=%s),
            (SELECT MAX(id) FROM advisory_source_reviews WHERE policy_revision=%s AND outcome='success')
        ) ORDER BY id DESC
    """,
        (policy["revision"], policy["revision"]),
    )
    reviews = [
        {**row, "reviewed_on": row["reviewed_on"].isoformat()} for row in cur.fetchall()
    ]
    return advisories.catalog_health(policy, reviews)


def get_advisory_source_policy():
    with _get_connection() as conn, conn.cursor() as cur:
        policy = _policy(cur)
        return {"policy": policy, "health": _health(cur, policy)}


def set_advisory_source_policy(payload):
    policy = advisories.validate_policy(payload)
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute("LOCK TABLE advisory_review_policies IN EXCLUSIVE MODE")
        cur.execute("SELECT COUNT(*) AS count FROM advisory_review_policies")
        if cur.fetchone()["count"] >= advisories.MAX_POLICY_REVISIONS:
            raise AdvisoryError("policy_limit_reached", 409)
        cur.execute(
            "INSERT INTO advisory_review_policies(policy) VALUES(%s) RETURNING revision",
            (Json(policy),),
        )
        saved = {"revision": cur.fetchone()["revision"], **policy}
        return {"policy": saved, "health": _health(cur, saved)}


def record_advisory_source_review(payload):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute("LOCK TABLE advisory_review_policies IN EXCLUSIVE MODE")
        policy = _policy(cur)
        if not policy:
            raise AdvisoryError("source_policy_required", 409)
        review = advisories.validate_source_review(payload, policy)
        cur.execute("SELECT COUNT(*) AS count FROM advisory_source_reviews")
        if cur.fetchone()["count"] >= advisories.MAX_SOURCE_REVIEWS:
            raise AdvisoryError("source_review_limit_reached", 409)
        cur.execute(
            """
            INSERT INTO advisory_source_reviews(policy_revision,outcome,reviewed_on,review_owner,failure_code,source_urls)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id
        """,
            (
                review["policy_revision"],
                review["outcome"],
                review["reviewed_on"],
                review["review_owner"],
                review["failure_code"],
                Json(review["source_urls"]),
            ),
        )
        return {"id": cur.fetchone()["id"], **review}


def list_advisory_source_reviews():
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM advisory_source_reviews ORDER BY id DESC LIMIT 50")
        return [
            {
                **row,
                "reviewed_on": row["reviewed_on"].isoformat(),
                "recorded_at": row["recorded_at"].isoformat(),
            }
            for row in cur.fetchall()
        ]


def _equipment(cur, actor, equipment_id):
    cur.execute(
        "SELECT verified FROM buildings WHERE building_id=%s FOR UPDATE", (actor,)
    )
    row = cur.fetchone()
    if not row or not row["verified"]:
        raise AdvisoryError("verified_owner_required", 403)
    cur.execute(
        "SELECT id,record FROM equipment_records WHERE id=%s AND owner_building_id=%s",
        (equipment_id, actor),
    )
    row = cur.fetchone()
    if not row:
        raise AdvisoryError("equipment_not_found", 404)
    return {"id": str(row["id"]), **row["record"]}


def _published(cur):
    cur.execute("""
        SELECT DISTINCT ON(r.advisory_id) r.*,p.reviewer_id,p.reviewed_on
        FROM advisory_revisions r INNER JOIN advisory_publications p
          ON p.advisory_id=r.advisory_id AND p.revision=r.revision
        ORDER BY r.advisory_id,r.revision DESC
    """)
    return [_revision(row) for row in cur.fetchall()]


def get_equipment_advisories(actor, equipment_id):
    with _get_connection() as conn, conn.cursor() as cur:
        equipment = _equipment(cur, actor, equipment_id)
        return _notices(cur, equipment)


def _actions(cur, equipment_id):
    cur.execute(
        """
        SELECT a.*,r.content->>'reference' AS reference,r.content->>'title' AS title,
               r.content->>'source_url' AS source_url
        FROM equipment_advisory_actions a
        INNER JOIN advisory_revisions r ON r.advisory_id=a.advisory_id AND r.revision=a.revision
        WHERE a.equipment_id=%s ORDER BY a.updated_at DESC LIMIT 100
    """,
        (equipment_id,),
    )
    return [
        {
            "advisory_id": str(row["advisory_id"]),
            "revision": row["revision"],
            "context_digest": row["context_digest"],
            "device_context": row["device_context"],
            "action": row["action"],
            "reference": row["reference"],
            "title": row["title"],
            "source_url": row["source_url"],
            "updated_at": row["updated_at"].isoformat(),
        }
        for row in cur.fetchall()
    ]


def _notices(cur, equipment):
    publications = _published(cur)
    cur.execute("""
        SELECT r.*,p.reviewer_id,p.reviewed_on FROM advisory_revisions r
        INNER JOIN advisory_publications p ON p.advisory_id=r.advisory_id AND p.revision=r.revision
        WHERE p.revision < (SELECT MAX(newer.revision) FROM advisory_publications newer WHERE newer.advisory_id=p.advisory_id)
        ORDER BY p.published_at DESC,p.advisory_id,p.revision DESC LIMIT 51
    """)
    previous = cur.fetchall()
    publications.extend(_revision(row) for row in previous[:50])
    health = _health(cur, _policy(cur))
    return advisories.assess_catalog(
        equipment,
        publications,
        _actions(cur, equipment["id"]),
        health,
        history_truncated=len(previous) > 50,
    )


def record_equipment_advisory_action(
    actor, equipment_id, advisory_id, revision, payload
):
    with _get_connection() as conn, conn.cursor() as cur:
        equipment = _equipment(cur, actor, equipment_id)
        cur.execute("LOCK TABLE advisory_review_policies IN SHARE MODE")
        cur.execute(
            "SELECT advisory_id FROM advisory_catalog WHERE advisory_id=%s FOR SHARE",
            (advisory_id,),
        )
        notices = _notices(cur, equipment)
        assessment = next(
            (
                item
                for item in notices["assessments"]
                if item["advisory_id"] == advisory_id and item["revision"] == revision
            ),
            None,
        )
        if not assessment or not assessment["action_allowed"]:
            raise AdvisoryError("notice_unavailable", 409)
        action = advisories.validate_owner_action(payload, notices["context_digest"])
        cur.execute(
            "SELECT COUNT(*) AS count FROM equipment_advisory_actions WHERE equipment_id=%s",
            (equipment_id,),
        )
        count = cur.fetchone()["count"]
        cur.execute(
            "SELECT 1 FROM equipment_advisory_actions WHERE equipment_id=%s AND advisory_id=%s AND revision=%s AND context_digest=%s",
            (equipment_id, advisory_id, revision, notices["context_digest"]),
        )
        if not cur.fetchone() and count >= 500:
            raise AdvisoryError("action_limit_reached", 409)
        cur.execute(
            """
            INSERT INTO equipment_advisory_actions(equipment_id,advisory_id,revision,context_digest,device_context,action)
            VALUES(%s,%s,%s,%s,%s,%s)
            ON CONFLICT(equipment_id,advisory_id,revision,context_digest)
            DO UPDATE SET action=EXCLUDED.action,updated_at=CURRENT_TIMESTAMP
        """,
            (
                equipment_id,
                advisory_id,
                revision,
                notices["context_digest"],
                Json(advisories.equipment_context(equipment)),
                Json(action),
            ),
        )
        return {"action": action, "context_digest": notices["context_digest"]}

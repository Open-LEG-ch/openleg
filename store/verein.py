# SPDX-License-Identifier: AGPL-3.0-or-later
"""Association records, deliberately separate from LEG participants and billing."""

import hashlib
import secrets
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from werkzeug.security import check_password_hash, generate_password_hash

_DUMMY_PASSWORD = generate_password_hash(secrets.token_urlsafe(32))


def now_utc():
    """Business clock; authentication expiry continues to use server time."""
    return datetime.now(UTC)


class VereinDenied(ValueError):
    """An ineligible or stale association action."""


class VereinUnavailable(RuntimeError):
    """Association storage failed; never substitute simulated success."""


@contextmanager
def _cursor():
    import database

    try:
        with database.get_connection() as conn, conn.cursor() as cur:
            yield cur
    except VereinDenied:
        raise
    except Exception as exc:
        raise VereinUnavailable("Association storage unavailable") from exc


def request_applicant_access(email):
    token = secrets.token_urlsafe(32)
    with _cursor() as cur:
        cur.execute(
            """SELECT id,preferred_locale FROM verein_applications
            WHERE email=%s AND confirmed_at IS NOT NULL AND status!='withdrawn' """,
            (email.strip().casefold(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            """INSERT INTO verein_applicant_access(token_hash,application_id,expires_at)
            VALUES (%s,%s,%s)""",
            (token_digest(token), row["id"], datetime.now(UTC) + timedelta(hours=1)),
        )
        return {"token": token, "locale": row["preferred_locale"]}


def consume_applicant_access(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise VereinDenied("Invalid access")
    with _cursor() as cur:
        cur.execute(
            """DELETE FROM verein_applicant_access WHERE token_hash=%s
            AND expires_at>CURRENT_TIMESTAMP RETURNING application_id""",
            (token_digest(token),),
        )
        row = cur.fetchone()
        if not row:
            raise VereinDenied("Invalid access")
        session_token = secrets.token_urlsafe(32)
        cur.execute(
            """INSERT INTO verein_applicant_sessions(token_hash,application_id,expires_at)
            VALUES (%s,%s,%s)""",
            (
                token_digest(session_token),
                row["application_id"],
                datetime.now(UTC) + timedelta(hours=1),
            ),
        )
        return session_token


def _applicant(cur, token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise VereinDenied("Applicant login required")
    cur.execute(
        """SELECT a.id FROM verein_applicant_sessions s JOIN verein_applications a
        ON a.id=s.application_id WHERE s.token_hash=%s AND s.expires_at>CURRENT_TIMESTAMP
        AND a.status!='withdrawn' """,
        (token_digest(token),),
    )
    row = cur.fetchone()
    if not row:
        raise VereinDenied("Applicant login required")
    return row["id"]


def applicant_profile(token):
    with _cursor() as cur:
        identifier = _applicant(cur, token)
        cur.execute(
            """SELECT a.id,a.name,a.email,a.status,a.decision_reason,m.status AS member_status,
            d.amount_chf,d.due_on,d.paid_at,d.reminder_sent_at
            FROM verein_applications a LEFT JOIN verein_members m ON m.application_id=a.id
            LEFT JOIN verein_dues d ON d.member_id=m.id WHERE a.id=%s
            ORDER BY d.period_start DESC LIMIT 1""",
            (identifier,),
        )
        record = dict(cur.fetchone())
        cur.execute(
            """SELECT id,kind,status,reason,resolution FROM verein_member_requests
            WHERE application_id=%s ORDER BY created_at DESC""",
            (identifier,),
        )
        record["requests"] = [dict(row) for row in cur.fetchall()]
        return record


def submit_member_request(token, kind, reason):
    if kind not in {"resignation", "appeal"} or not 3 <= len(reason.strip()) <= 2000:
        raise VereinDenied("Invalid member request")
    with _cursor() as cur:
        identifier = _applicant(cur, token)
        cur.execute(
            """SELECT a.status,m.status AS member_status FROM verein_applications a
            LEFT JOIN verein_members m ON m.application_id=a.id WHERE a.id=%s FOR UPDATE OF a""",
            (identifier,),
        )
        record = cur.fetchone()
        if kind == "resignation" and record["member_status"] != "active":
            raise VereinDenied("Active membership required")
        if (
            kind == "appeal"
            and record["status"] != "refused"
            and record["member_status"] != "terminated"
        ):
            raise VereinDenied("Adverse decision required")
        cur.execute(
            """INSERT INTO verein_member_requests(id,application_id,kind,reason)
            VALUES (%s,%s,%s,%s) ON CONFLICT (application_id,kind) WHERE status='pending'
            DO NOTHING RETURNING id""",
            (str(uuid.uuid4()), identifier, kind, reason.strip()),
        )
        if cur.fetchone():
            cur.execute(
                "INSERT INTO verein_events(application_id,action) VALUES (%s,%s)",
                (identifier, kind + "_requested"),
            )


def list_member_requests(token):
    with _cursor() as cur:
        _reviewer(cur, token)
        cur.execute("""SELECT r.id,r.application_id,r.kind,r.reason,r.created_at,a.name
            FROM verein_member_requests r JOIN verein_applications a ON a.id=r.application_id
            WHERE r.status='pending' ORDER BY r.created_at LIMIT 100""")
        return [dict(row) for row in cur.fetchall()]


def resolve_member_request(token, identifier, outcome, reason, evidence):
    if (
        outcome not in {"approved", "dismissed"}
        or not 3 <= len(reason.strip()) <= 2000
        or not 3 <= len(evidence.strip()) <= 500
    ):
        raise VereinDenied("Reason and actual resolution evidence required")
    try:
        uuid.UUID(identifier)
    except (TypeError, ValueError):
        raise VereinDenied("Invalid request") from None
    now = now_utc()
    with _cursor() as cur:
        actor = _reviewer(cur, token)
        cur.execute(
            "SELECT * FROM verein_member_requests WHERE id=%s AND status='pending' FOR UPDATE",
            (identifier,),
        )
        record = cur.fetchone()
        if not record:
            raise VereinDenied("Pending request required")
        app_id = record["application_id"]
        if record["kind"] == "resignation" and outcome == "approved":
            cur.execute(
                """UPDATE verein_members SET status='resigned',ended_at=%s,end_reason=%s,
                end_reviewer=%s WHERE application_id=%s AND status='active' """,
                (now, reason, actor["id"], app_id),
            )
        if record["kind"] == "appeal" and outcome == "approved":
            cur.execute(
                "SELECT id FROM verein_members WHERE application_id=%s FOR UPDATE",
                (app_id,),
            )
            member = cur.fetchone()
            if member:
                cur.execute(
                    "UPDATE verein_members SET status='active',ended_at=NULL,end_reason=NULL,end_reviewer=NULL WHERE id=%s",
                    (member["id"],),
                )
            else:
                member_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO verein_members(id,application_id,accepted_at) VALUES (%s,%s,%s)",
                    (member_id, app_id, now),
                )
                day = now.astimezone(ZoneInfo("Europe/Zurich")).date()
                cur.execute(
                    "INSERT INTO verein_dues(id,member_id,period_start,amount_chf,due_on) VALUES (%s,%s,%s,50,%s)",
                    (str(uuid.uuid4()), member_id, day, day + timedelta(days=30)),
                )
                cur.execute(
                    "UPDATE verein_applications SET status='accepted' WHERE id=%s",
                    (app_id,),
                )
        cur.execute(
            """UPDATE verein_member_requests SET status=%s,resolved_at=%s,resolution=%s,
            evidence_reference=%s,reviewer_id=%s WHERE id=%s""",
            (outcome, now, reason, evidence, actor["id"], identifier),
        )
        cur.execute(
            "INSERT INTO verein_events(application_id,actor_id,action,reason) VALUES (%s,%s,%s,%s)",
            (app_id, actor["id"], record["kind"] + "_" + outcome, reason),
        )


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verein_applications (
            id UUID PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            affiliation TEXT NOT NULL DEFAULT '',
            preferred_locale TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            privacy_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unconfirmed' CHECK (status IN
                ('unconfirmed','pending','accepted','refused','withdrawn')),
            confirmation_hash TEXT UNIQUE,
            confirmation_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            confirmed_at TIMESTAMPTZ,
            decided_at TIMESTAMPTZ,
            decision_reason TEXT,
            decision_reviewer UUID
        );
        CREATE TABLE IF NOT EXISTS verein_events (
            id BIGSERIAL PRIMARY KEY,
            application_id UUID REFERENCES verein_applications(id) ON DELETE CASCADE,
            actor_id UUID,
            action TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS verein_reviewers (
            id UUID PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            password_hash TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
            role TEXT NOT NULL DEFAULT 'board' CHECK (role IN ('board','finance','community'))
        );
        CREATE TABLE IF NOT EXISTS verein_sessions (
            token_hash TEXT PRIMARY KEY,
            reviewer_id UUID NOT NULL REFERENCES verein_reviewers(id),
            expires_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS verein_members (
            id UUID PRIMARY KEY,
            application_id UUID NOT NULL UNIQUE REFERENCES verein_applications(id),
            accepted_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','resigned','terminated')),
            ended_at TIMESTAMPTZ,
            end_reason TEXT,
            end_reviewer UUID REFERENCES verein_reviewers(id)
        );
        CREATE TABLE IF NOT EXISTS verein_dues (
            id UUID PRIMARY KEY,
            member_id UUID NOT NULL REFERENCES verein_members(id),
            period_start DATE NOT NULL,
            amount_chf NUMERIC(12,2) NOT NULL CHECK (amount_chf=50),
            due_on DATE NOT NULL,
            reminder_sent_at TIMESTAMPTZ,
            paid_at TIMESTAMPTZ,
            payment_reference TEXT UNIQUE,
            UNIQUE(member_id,period_start)
        );
        CREATE TABLE IF NOT EXISTS verein_notifications (
            id UUID PRIMARY KEY,
            application_id UUID NOT NULL REFERENCES verein_applications(id),
            kind TEXT NOT NULL,
            dues_id UUID REFERENCES verein_dues(id),
            event_id BIGINT NOT NULL UNIQUE REFERENCES verein_events(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0,
            UNIQUE(dues_id,kind)
        );
        CREATE TABLE IF NOT EXISTS verein_applicant_access (
            token_hash TEXT PRIMARY KEY,
            application_id UUID NOT NULL REFERENCES verein_applications(id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS verein_applicant_sessions (
            token_hash TEXT PRIMARY KEY,
            application_id UUID NOT NULL REFERENCES verein_applications(id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS verein_member_requests (
            id UUID PRIMARY KEY,
            application_id UUID NOT NULL REFERENCES verein_applications(id),
            kind TEXT NOT NULL CHECK (kind IN ('resignation','appeal')),
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','dismissed')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMPTZ,
            resolution TEXT,
            evidence_reference TEXT,
            reviewer_id UUID REFERENCES verein_reviewers(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS verein_one_pending_request
            ON verein_member_requests(application_id,kind) WHERE status='pending';
    """)


def token_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def apply(*, email, name, affiliation, locale, privacy_version):
    """Save or retry an unconfirmed application without exposing an existing member."""
    identifier = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with _cursor() as cur:
        cur.execute(
            """INSERT INTO verein_applications
                (id,email,name,affiliation,preferred_locale,policy_version,
                 privacy_version,confirmation_hash,confirmation_expires_at,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (email) DO NOTHING RETURNING id""",
            (
                identifier,
                email,
                name,
                affiliation,
                locale,
                "2026-11-04",
                privacy_version,
                token_digest(token),
                now + timedelta(hours=24),
                now,
            ),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "INSERT INTO verein_events (application_id,action) VALUES (%s,'submitted')",
                (identifier,),
            )
            return token
        cur.execute(
            """UPDATE verein_applications
                SET confirmation_hash=%s,confirmation_expires_at=%s
                WHERE email=%s AND status='unconfirmed' RETURNING id""",
            (token_digest(token), now + timedelta(hours=24), email),
        )
        return token if cur.fetchone() else None


def confirm(token):
    """Consume one live token; email confirmation does not admit a member."""
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise VereinDenied("Invalid confirmation")
    with _cursor() as cur:
        cur.execute(
            """UPDATE verein_applications
                SET status='pending',confirmed_at=CURRENT_TIMESTAMP,
                    confirmation_hash=NULL,confirmation_expires_at=NULL
                WHERE confirmation_hash=%s AND status='unconfirmed'
                    AND confirmation_expires_at>CURRENT_TIMESTAMP RETURNING id""",
            (token_digest(token),),
        )
        row = cur.fetchone()
        if not row:
            raise VereinDenied("Invalid confirmation")
        cur.execute(
            "INSERT INTO verein_events (application_id,action) VALUES (%s,'confirmed')",
            (row["id"],),
        )


def provision_reviewer(email, name, password, role="board"):
    """Local provisioning command only; never an anonymous HTTP operation."""
    if len(password) < 16 or role not in {"board", "finance", "community"}:
        raise VereinDenied("Invalid reviewer credentials")
    identifier = str(uuid.uuid4())
    password_hash = generate_password_hash(password)
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO verein_reviewers (id,email,name,password_hash,role) VALUES (%s,%s,%s,%s,%s)",
            (identifier, email.strip().casefold(), name, password_hash, role),
        )
    return identifier


def login(email, password):
    with _cursor() as cur:
        cur.execute(
            "SELECT id,password_hash,active FROM verein_reviewers WHERE email=%s",
            (email.strip().casefold(),),
        )
        reviewer = cur.fetchone()
        valid = check_password_hash(
            reviewer["password_hash"] if reviewer else _DUMMY_PASSWORD, password
        )
        if not valid or not reviewer or not reviewer["active"]:
            raise VereinDenied("Invalid reviewer credentials")
        token = secrets.token_urlsafe(32)
        cur.execute(
            "INSERT INTO verein_sessions (token_hash,reviewer_id,expires_at) VALUES (%s,%s,%s)",
            (
                token_digest(token),
                reviewer["id"],
                datetime.now(UTC) + timedelta(hours=8),
            ),
        )
        return token


def _reviewer(cur, token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise VereinDenied("Reviewer login required")
    cur.execute(
        """SELECT r.id,r.email,r.name,r.role FROM verein_sessions s
        JOIN verein_reviewers r ON r.id=s.reviewer_id
        WHERE s.token_hash=%s AND s.expires_at>CURRENT_TIMESTAMP AND r.active=TRUE
        FOR SHARE OF r""",
        (token_digest(token),),
    )
    reviewer = cur.fetchone()
    if not reviewer:
        raise VereinDenied("Reviewer login required")
    return reviewer


def reviewer(token):
    with _cursor() as cur:
        return _reviewer(cur, token)


def logout(token):
    if not isinstance(token, str):
        return
    with _cursor() as cur:
        cur.execute(
            "DELETE FROM verein_sessions WHERE token_hash=%s", (token_digest(token),)
        )


def revoke_reviewer(identifier):
    """Local board-turnover command; sessions cease to work immediately."""
    with _cursor() as cur:
        cur.execute(
            "UPDATE verein_reviewers SET active=FALSE WHERE id=%s", (identifier,)
        )
        cur.execute("DELETE FROM verein_sessions WHERE reviewer_id=%s", (identifier,))


def list_applications(token):
    with _cursor() as cur:
        _reviewer(cur, token)
        cur.execute("""SELECT id,name,email,status,created_at,confirmed_at
            FROM verein_applications WHERE status='pending' ORDER BY created_at,id LIMIT 100""")
        return [dict(row) for row in cur.fetchall()]


def application(token, identifier):
    try:
        uuid.UUID(identifier)
    except (ValueError, TypeError):
        raise VereinDenied("Invalid application") from None
    with _cursor() as cur:
        _reviewer(cur, token)
        cur.execute(
            """SELECT a.id,a.name,a.email,a.affiliation,a.preferred_locale,a.status,
                a.created_at,a.confirmed_at,a.decided_at,a.decision_reason,
                m.id AS member_id,m.status AS member_status,d.amount_chf,d.due_on,d.paid_at,
                d.reminder_sent_at,d.id AS dues_id
            FROM verein_applications a LEFT JOIN verein_members m ON m.application_id=a.id
            LEFT JOIN verein_dues d ON d.member_id=m.id
            WHERE a.id=%s ORDER BY d.period_start DESC LIMIT 1""",
            (identifier,),
        )
        row = cur.fetchone()
        if not row:
            raise VereinDenied("Invalid application")
        return dict(row)


def decide(token, identifier, decision, reason):
    if decision not in {"accepted", "refused"} or not 3 <= len(reason.strip()) <= 2000:
        raise VereinDenied("Invalid decision")
    try:
        uuid.UUID(identifier)
    except (ValueError, TypeError):
        raise VereinDenied("Invalid application") from None
    now = now_utc()
    with _cursor() as cur:
        actor = _reviewer(cur, token)
        cur.execute(
            "SELECT id,status FROM verein_applications WHERE id=%s FOR UPDATE",
            (identifier,),
        )
        row = cur.fetchone()
        if not row or row["status"] != "pending":
            raise VereinDenied("Application is not pending")
        cur.execute(
            """UPDATE verein_applications SET status=%s,decided_at=%s,
            decision_reason=%s,decision_reviewer=%s WHERE id=%s""",
            (decision, now, reason.strip(), actor["id"], identifier),
        )
        if decision == "accepted":
            member = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO verein_members (id,application_id,accepted_at) VALUES (%s,%s,%s)",
                (member, identifier, now),
            )
            cur.execute(
                """INSERT INTO verein_dues (id,member_id,period_start,amount_chf,due_on)
                VALUES (%s,%s,%s,50,%s)""",
                (
                    str(uuid.uuid4()),
                    member,
                    now.astimezone(ZoneInfo("Europe/Zurich")).date(),
                    (
                        now.astimezone(ZoneInfo("Europe/Zurich")) + timedelta(days=30)
                    ).date(),
                ),
            )
        cur.execute(
            """INSERT INTO verein_events (application_id,actor_id,action,reason)
            VALUES (%s,%s,%s,%s) RETURNING id""",
            (identifier, actor["id"], decision, reason.strip()),
        )
        event = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO verein_notifications (id,application_id,kind,event_id) VALUES (%s,%s,%s,%s)",
            (str(uuid.uuid4()), identifier, decision, event),
        )


def pending_notifications(token):
    with _cursor() as cur:
        _reviewer(cur, token)
        cur.execute("""SELECT n.id,n.kind,a.email,a.preferred_locale,e.reason AS decision_reason
            FROM verein_notifications n JOIN verein_applications a ON a.id=n.application_id
            JOIN verein_events e ON e.id=n.event_id
            WHERE n.sent_at IS NULL ORDER BY n.created_at LIMIT 50""")
        return [dict(row) for row in cur.fetchall()]


def record_delivery(token, identifier, sent):
    with _cursor() as cur:
        _reviewer(cur, token)
        now = now_utc()
        cur.execute(
            """UPDATE verein_notifications SET attempts=attempts+1,
            sent_at=CASE WHEN %s THEN %s ELSE sent_at END
            WHERE id=%s AND sent_at IS NULL RETURNING kind,dues_id""",
            (sent, now, identifier),
        )
        notice = cur.fetchone()
        if sent and notice and notice["kind"] == "reminder":
            cur.execute(
                """UPDATE verein_dues SET reminder_sent_at=%s
                WHERE id=%s AND paid_at IS NULL AND reminder_sent_at IS NULL""",
                (now, notice["dues_id"]),
            )


def _dues(cur, identifier):
    try:
        uuid.UUID(identifier)
    except (TypeError, ValueError):
        raise VereinDenied("Invalid application") from None
    cur.execute(
        """SELECT d.*,m.status AS member_status,m.id AS membership_id
        FROM verein_dues d JOIN verein_members m ON d.member_id=m.id
        WHERE m.application_id=%s ORDER BY d.period_start DESC LIMIT 1 FOR UPDATE OF d,m""",
        (identifier,),
    )
    row = cur.fetchone()
    if not row or row["member_status"] != "active":
        raise VereinDenied("Active member required")
    return row


def request_reminder(token, identifier):
    now = now_utc()
    with _cursor() as cur:
        actor = _reviewer(cur, token)
        dues = _dues(cur, identifier)
        if (
            dues["paid_at"]
            or now.astimezone(ZoneInfo("Europe/Zurich")).date() <= dues["due_on"]
        ):
            raise VereinDenied("Dues are not overdue")
        cur.execute(
            "SELECT id FROM verein_notifications WHERE dues_id=%s AND kind='reminder'",
            (dues["id"],),
        )
        if cur.fetchone():
            return
        cur.execute(
            """INSERT INTO verein_events (application_id,actor_id,action)
            VALUES (%s,%s,'reminder_requested') RETURNING id""",
            (identifier, actor["id"]),
        )
        event = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO verein_notifications (id,application_id,kind,dues_id,event_id)
            VALUES (%s,%s,'reminder',%s,%s)""",
            (str(uuid.uuid4()), identifier, dues["id"], event),
        )


def terminate(token, identifier, reason):
    if not 3 <= len(reason.strip()) <= 2000:
        raise VereinDenied("Reason required")
    now = now_utc()
    with _cursor() as cur:
        actor = _reviewer(cur, token)
        dues = _dues(cur, identifier)
        if (
            dues["paid_at"]
            or not dues["reminder_sent_at"]
            or now < dues["reminder_sent_at"] + timedelta(days=60)
        ):
            raise VereinDenied("Additional reminder period has not elapsed")
        cur.execute(
            """UPDATE verein_members SET status='terminated',ended_at=%s,
            end_reason=%s,end_reviewer=%s WHERE id=%s""",
            (now, reason, actor["id"], dues["membership_id"]),
        )
        cur.execute(
            """INSERT INTO verein_events (application_id,actor_id,action,reason)
            VALUES (%s,%s,'terminated',%s) RETURNING id""",
            (identifier, actor["id"], reason),
        )
        event = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO verein_notifications (id,application_id,kind,event_id)
            VALUES (%s,%s,'terminated',%s)""",
            (str(uuid.uuid4()), identifier, event),
        )

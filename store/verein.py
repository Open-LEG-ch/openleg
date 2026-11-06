# SPDX-License-Identifier: AGPL-3.0-or-later
"""Association records, deliberately separate from LEG participants and billing."""

import hashlib
import secrets
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

import verein_policy

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


def logout_applicant(token):
    if not isinstance(token, str):
        return
    with _cursor() as cur:
        cur.execute(
            "DELETE FROM verein_applicant_sessions WHERE token_hash=%s",
            (token_digest(token),),
        )


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
            """SELECT a.id,a.name,a.email,a.status,
            CASE WHEN m.status IN ('terminated','resigned') THEN m.end_reason ELSE a.decision_reason END AS decision_reason,
            CASE WHEN m.status IN ('terminated','resigned') THEN m.ended_at ELSE a.decided_at END AS decision_at,
            m.status AS member_status,
            d.amount_chf,d.due_on,d.paid_at,d.reminder_sent_at,
            (SELECT COALESCE(SUM(r.amount_chf),0) FROM verein_dues_receipts r WHERE r.dues_id=d.id) AS received_chf
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
            "SELECT application_id FROM verein_member_requests WHERE id=%s AND status='pending'",
            (identifier,),
        )
        requested = cur.fetchone()
        if not requested:
            raise VereinDenied("Pending request required")
        app_id = requested["application_id"]
        cur.execute(
            "SELECT status FROM verein_applications WHERE id=%s FOR UPDATE", (app_id,)
        )
        application_status = cur.fetchone()["status"]
        cur.execute(
            "SELECT * FROM verein_member_requests WHERE id=%s AND status='pending' FOR UPDATE",
            (identifier,),
        )
        record = cur.fetchone()
        if not record:
            raise VereinDenied("Pending request required")
        if record["kind"] == "resignation" and outcome == "approved":
            cur.execute(
                """UPDATE verein_members SET status='resigned',ended_at=%s,end_reason=%s,
                end_reviewer=%s WHERE application_id=%s AND status='active' """,
                (now, reason, actor["id"], app_id),
            )
            if cur.rowcount != 1:
                raise VereinDenied("Active membership required")
        if record["kind"] == "appeal" and outcome == "approved":
            cur.execute(
                "SELECT id,status FROM verein_members WHERE application_id=%s FOR UPDATE",
                (app_id,),
            )
            member = cur.fetchone()
            if member:
                if member["status"] != "terminated":
                    raise VereinDenied(
                        "Appeal no longer concerns a terminated membership"
                    )
                cur.execute(
                    "UPDATE verein_members SET status='active',ended_at=NULL,end_reason=NULL,end_reviewer=NULL WHERE id=%s",
                    (member["id"],),
                )
                # A successful appeal invalidates the challenged reminder period.
                cur.execute(
                    "UPDATE verein_dues SET reminder_sent_at=NULL WHERE member_id=%s AND paid_at IS NULL",
                    (member["id"],),
                )
            else:
                if application_status != "refused":
                    raise VereinDenied(
                        "Appeal no longer concerns a refused application"
                    )
                _admit(cur, app_id, now)
            cur.execute(
                """UPDATE verein_applications SET status='accepted',decided_at=%s,
                decision_reason=%s,decision_reviewer=%s WHERE id=%s""",
                (now, reason, actor["id"], app_id),
            )
        cur.execute(
            """UPDATE verein_notifications SET superseded_at=%s
            WHERE application_id=%s AND sent_at IS NULL AND superseded_at IS NULL""",
            (now, app_id),
        )
        cur.execute(
            """UPDATE verein_member_requests SET status=%s,resolved_at=%s,resolution=%s,
            evidence_reference=%s,reviewer_id=%s WHERE id=%s""",
            (outcome, now, reason, evidence, actor["id"], identifier),
        )
        cur.execute(
            "INSERT INTO verein_events(application_id,actor_id,action,reason) VALUES (%s,%s,%s,%s) RETURNING id",
            (app_id, actor["id"], record["kind"] + "_" + outcome, reason),
        )
        event_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO verein_notifications(id,application_id,kind,event_id) VALUES (%s,%s,%s,%s)",
            (str(uuid.uuid4()), app_id, record["kind"] + "_" + outcome, event_id),
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
            superseded_at TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS verein_current_dues_notice
            ON verein_notifications(dues_id,kind) WHERE superseded_at IS NULL;
        CREATE TABLE IF NOT EXISTS verein_dues_receipts (
            id UUID PRIMARY KEY,
            dues_id UUID NOT NULL REFERENCES verein_dues(id),
            amount_chf NUMERIC(12,2) NOT NULL CHECK (amount_chf>0 AND amount_chf<=50),
            source TEXT NOT NULL,
            reference TEXT NOT NULL,
            evidence_reference TEXT NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL,
            reviewer_id UUID NOT NULL REFERENCES verein_reviewers(id),
            UNIQUE(source,reference)
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
                    ,name=%s,affiliation=%s,preferred_locale=%s,privacy_version=%s,policy_version=%s
                WHERE email=%s AND status='unconfirmed' RETURNING id""",
            (
                token_digest(token),
                now + timedelta(hours=24),
                name,
                affiliation,
                locale,
                privacy_version,
                "2026-11-04",
                email,
            ),
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


def list_applications(token, status="pending", page=1):
    if (
        status
        not in {
            "pending",
            "accepted",
            "refused",
            "active",
            "resigned",
            "terminated",
            "all",
        }
        or not isinstance(page, int)
        or not 1 <= page <= 100000
    ):
        raise VereinDenied("Invalid application filter")
    with _cursor() as cur:
        _reviewer(cur, token)
        cur.execute(
            """SELECT a.id,a.name,a.email,a.status,a.created_at,a.confirmed_at,m.status AS member_status
            FROM verein_applications a LEFT JOIN verein_members m ON m.application_id=a.id
            WHERE a.confirmed_at IS NOT NULL AND (%s='all' OR a.status=%s OR m.status=%s)
            ORDER BY a.created_at,a.id LIMIT 100 OFFSET %s""",
            (status, status, status, (page - 1) * 100),
        )
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
                d.reminder_sent_at,d.id AS dues_id,m.end_reason,m.ended_at,
                (SELECT COALESCE(SUM(r.amount_chf),0) FROM verein_dues_receipts r WHERE r.dues_id=d.id) AS received_chf
            FROM verein_applications a LEFT JOIN verein_members m ON m.application_id=a.id
            LEFT JOIN verein_dues d ON d.member_id=m.id
            WHERE a.id=%s ORDER BY d.period_start DESC LIMIT 1""",
            (identifier,),
        )
        row = cur.fetchone()
        if not row:
            raise VereinDenied("Invalid application")
        return dict(row)


def _admit(cur, identifier, now):
    member = str(uuid.uuid4())
    day, due_on = verein_policy.admission_dates(now)
    cur.execute(
        "INSERT INTO verein_members(id,application_id,accepted_at) VALUES (%s,%s,%s)",
        (member, identifier, now),
    )
    cur.execute(
        "INSERT INTO verein_dues(id,member_id,period_start,amount_chf,due_on) VALUES (%s,%s,%s,%s,%s)",
        (str(uuid.uuid4()), member, day, verein_policy.ANNUAL_DUES_CHF, due_on),
    )


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
            _admit(cur, identifier, now)
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
        cur.execute("""SELECT n.id,n.kind,a.email,a.preferred_locale,e.reason AS decision_reason,
            (SELECT d.due_on FROM verein_dues d JOIN verein_members m ON m.id=d.member_id
             WHERE m.application_id=a.id ORDER BY d.period_start DESC LIMIT 1) AS due_on
            FROM verein_notifications n JOIN verein_applications a ON a.id=n.application_id
            JOIN verein_events e ON e.id=n.event_id
            WHERE n.sent_at IS NULL AND n.superseded_at IS NULL ORDER BY n.created_at LIMIT 50""")
        return [dict(row) for row in cur.fetchall()]


def record_delivery(token, identifier, sent):
    with _cursor() as cur:
        _reviewer(cur, token)
        now = now_utc()
        cur.execute(
            """UPDATE verein_notifications SET attempts=attempts+1,
            sent_at=CASE WHEN %s THEN %s ELSE sent_at END
            WHERE id=%s AND sent_at IS NULL AND superseded_at IS NULL RETURNING kind,dues_id""",
            (sent, now, identifier),
        )
        notice = cur.fetchone()
        if sent and notice and notice["kind"] == "reminder":
            cur.execute(
                """UPDATE verein_dues SET reminder_sent_at=%s
                WHERE id=%s AND paid_at IS NULL AND reminder_sent_at IS NULL""",
                (now, notice["dues_id"]),
            )


def _dues(cur, identifier, *, require_active=True):
    try:
        uuid.UUID(identifier)
    except (TypeError, ValueError):
        raise VereinDenied("Invalid application") from None
    cur.execute(
        "SELECT id FROM verein_applications WHERE id=%s FOR UPDATE", (identifier,)
    )
    cur.execute(
        """SELECT d.*,m.status AS member_status,m.id AS membership_id
        FROM verein_dues d JOIN verein_members m ON d.member_id=m.id
        WHERE m.application_id=%s ORDER BY d.period_start DESC LIMIT 1 FOR UPDATE OF d,m""",
        (identifier,),
    )
    row = cur.fetchone()
    if not row or (require_active and row["member_status"] != "active"):
        raise VereinDenied("Active member required")
    return row


def record_payment(token, identifier, *, amount_chf, source, reference, evidence):
    """Record an operator-verified incoming receipt; no redirect settles dues."""
    try:
        amount = verein_policy.received_amount(amount_chf)
    except ValueError:
        raise VereinDenied("Invalid received amount") from None
    if (
        not 3 <= len(source.strip()) <= 120
        or not 3 <= len(reference.strip()) <= 200
        or not 3 <= len(evidence.strip()) <= 500
    ):
        raise VereinDenied("Source, unique reference and private evidence required")
    source, reference, evidence = source.strip(), reference.strip(), evidence.strip()
    now = now_utc()
    with _cursor() as cur:
        actor = _reviewer(cur, token)
        dues = _dues(cur, identifier, require_active=False)
        cur.execute(
            """SELECT r.amount_chf,r.evidence_reference,m.application_id
            FROM verein_dues_receipts r JOIN verein_dues d ON d.id=r.dues_id
            JOIN verein_members m ON m.id=d.member_id WHERE r.source=%s AND r.reference=%s""",
            (source, reference),
        )
        existing = cur.fetchone()
        if existing:
            if (
                str(existing["application_id"]) != identifier
                or existing["amount_chf"] != amount
                or existing["evidence_reference"] != evidence
            ):
                raise VereinDenied("Receipt reference conflicts with existing evidence")
            return
        cur.execute(
            "SELECT COALESCE(SUM(amount_chf),0) AS total FROM verein_dues_receipts WHERE dues_id=%s",
            (dues["id"],),
        )
        total = cur.fetchone()["total"] + amount
        if total > dues["amount_chf"]:
            raise VereinDenied("Receipt exceeds outstanding dues")
        cur.execute(
            """INSERT INTO verein_dues_receipts(id,dues_id,amount_chf,source,reference,evidence_reference,recorded_at,reviewer_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (source,reference) DO NOTHING RETURNING id""",
            (
                str(uuid.uuid4()),
                dues["id"],
                amount,
                source,
                reference,
                evidence,
                now,
                actor["id"],
            ),
        )
        if not cur.fetchone():
            raise VereinDenied("Receipt already recorded")
        if total == dues["amount_chf"]:
            cur.execute(
                "UPDATE verein_dues SET paid_at=%s WHERE id=%s", (now, dues["id"])
            )
            cur.execute(
                "UPDATE verein_notifications SET superseded_at=%s WHERE dues_id=%s AND kind='reminder' AND sent_at IS NULL",
                (now, dues["id"]),
            )
        cur.execute(
            "INSERT INTO verein_events(application_id,actor_id,action,reason) VALUES (%s,%s,'dues_received',%s)",
            (identifier, actor["id"], evidence),
        )


def request_reminder(token, identifier):
    now = now_utc()
    with _cursor() as cur:
        actor = _reviewer(cur, token)
        dues = _dues(cur, identifier)
        if not verein_policy.can_remind(
            now=now, due_on=dues["due_on"], paid_at=dues["paid_at"]
        ):
            raise VereinDenied("Dues are not overdue")
        cur.execute(
            "SELECT id FROM verein_notifications WHERE dues_id=%s AND kind='reminder' AND superseded_at IS NULL",
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
        if not verein_policy.can_terminate(
            now=now, reminder_sent_at=dues["reminder_sent_at"], paid_at=dues["paid_at"]
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

"""
Formation Wizard Module for OpenLEG
Handles LEG community formation workflow, document generation, and status tracking.
"""
import os
import uuid
import time
import logging
import io
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from enum import Enum
import event_hooks

logger = logging.getLogger(__name__)

class FormationStatus(Enum):
    """LEG formation workflow states."""
    INTERESTED = "interested"           # User registered, not in community
    INVITED = "invited"                 # Invited to join community
    CONFIRMED = "confirmed"             # Confirmed participation
    FORMATION_STARTED = "formation_started"  # Community formation initiated
    DOCUMENTS_GENERATED = "documents_generated"  # Contracts ready
    SIGNATURES_PENDING = "signatures_pending"    # Waiting for signatures
    DSO_SUBMITTED = "dso_submitted"     # DSO notification sent
    DSO_APPROVED = "dso_approved"       # DSO approved
    ACTIVE = "active"                   # Community operational
    REJECTED = "rejected"               # Formation failed/rejected

class DistributionModel(Enum):
    """Energy distribution models."""
    SIMPLE = "simple"                   # Equal distribution
    PROPORTIONAL = "proportional"       # Based on consumption/production
    CUSTOM = "custom"                   # Custom rules

# Formation configuration
FORMATION_CONFIG = {
    "min_community_size": 3,
    "max_community_size": 50,
    "formation_fee_chf": 0,
    "servicing_fee_monthly_chf": 0,
    "dso_response_days": 30,
    "signature_timeout_days": 14,
}

def get_contract_templates(jurisdiction="Kanton Zürich", dso_contact="EKZ Verteilnetz AG"):
    """Return contract templates parameterized by jurisdiction and DSO."""
    return {
        "community_agreement": {
            "title": "Lokale Elektrizitätsgemeinschaft - Gemeinschaftsvereinbarung",
            "jurisdiction": jurisdiction,
            "language": "de",
            "sections": [
                "parties", "purpose", "territory", "participation",
                "distribution_model", "metering", "billing",
                "liability", "termination", "governing_law"
            ]
        },
        "participant_contract": {
            "title": "Teilnehmervertrag LEG",
            "jurisdiction": jurisdiction,
            "language": "de",
            "sections": [
                "participant_info", "community_info", "obligations",
                "payment_terms", "termination"
            ]
        },
        "dso_notification": {
            "title": "Anmeldung Lokale Elektrizitätsgemeinschaft",
            "recipient": dso_contact,
            "form_id": "LEG-DSO-001",
            "sections": [
                "community_details", "participants", "grid_connection",
                "metering_setup", "start_date"
            ]
        }
    }

# Default templates (backward compatible)
CONTRACT_TEMPLATES = get_contract_templates()


def create_community(
    db,
    name: str,
    admin_building_id: str,
    distribution_model: str = "simple",
    description: str = ""
) -> Optional[Dict]:
    """
    Create a new LEG community.
    
    Args:
        db: Database module
        name: Community name
        admin_building_id: Building ID of the community admin
        distribution_model: Distribution model (simple/proportional/custom)
        description: Optional description
    
    Returns:
        Community dict or None if failed
    """
    try:
        community_id = str(uuid.uuid4())
        
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO communities (
                        community_id, name, admin_building_id, distribution_model,
                        description, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (
                    community_id, name, admin_building_id, distribution_model,
                    description, FormationStatus.INTERESTED.value
                ))
                
                # Add admin as first member
                cur.execute("""
                    INSERT INTO community_members (
                        community_id, building_id, role, status, joined_at
                    ) VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """, (community_id, admin_building_id, "admin", "confirmed"))
                
                logger.info(f"[FORMATION] Created community {community_id} by {admin_building_id}")
                
                return {
                    "community_id": community_id,
                    "name": name,
                    "admin_building_id": admin_building_id,
                    "distribution_model": distribution_model,
                    "status": FormationStatus.INTERESTED.value,
                    "member_count": 1
                }
    except Exception as e:
        logger.error(f"[FORMATION] Error creating community: {e}")
        return None


def invite_member(
    db,
    community_id: str,
    building_id: str,
    invited_by: str
) -> bool:
    """
    Invite a building to join a community.
    
    Args:
        db: Database module
        community_id: Community ID
        building_id: Building ID to invite
        invited_by: Building ID of inviter
    
    Returns:
        True if successful
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Check if already member
                cur.execute("""
                    SELECT 1 FROM community_members
                    WHERE community_id = %s AND building_id = %s
                """, (community_id, building_id))
                
                if cur.fetchone():
                    logger.warning(f"[FORMATION] Building {building_id} already in community {community_id}")
                    return False
                
                # Add as invited member
                cur.execute("""
                    INSERT INTO community_members (
                        community_id, building_id, role, status, invited_by, joined_at
                    ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """, (community_id, building_id, "member", "invited", invited_by))
                
                # Track event
                db.track_event("member_invited", building_id, {
                    "community_id": community_id,
                    "invited_by": invited_by
                })
                
                logger.info(f"[FORMATION] Invited {building_id} to community {community_id}")
                return True
    except Exception as e:
        logger.error(f"[FORMATION] Error inviting member: {e}")
        return False


def confirm_membership(
    db,
    community_id: str,
    building_id: str
) -> bool:
    """
    Confirm membership after invitation.
    
    Args:
        db: Database module
        community_id: Community ID
        building_id: Building ID confirming
    
    Returns:
        True if successful
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE community_members
                    SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP
                    WHERE community_id = %s AND building_id = %s AND status = 'invited'
                """, (community_id, building_id))
                
                if cur.rowcount > 0:
                    db.track_event("member_confirmed", building_id, {
                        "community_id": community_id
                    })
                    event_hooks.fire('member_confirmed', {
                        'community_id': community_id,
                        'building_id': building_id
                    })
                    logger.info(f"[FORMATION] {building_id} confirmed membership in {community_id}")

                    # Check formation threshold
                    cur.execute(
                        "SELECT COUNT(*) AS confirmed_count FROM community_members WHERE community_id = %s AND status = 'confirmed'",
                        (community_id,)
                    )
                    row = cur.fetchone()
                    confirmed_count = row['confirmed_count'] if row else 0
                    if confirmed_count >= 3:
                        event_hooks.fire('formation_threshold_reached', {
                            'community_id': community_id,
                            'confirmed_count': confirmed_count
                        })
                        logger.info(f"[FORMATION] Threshold reached for {community_id}: {confirmed_count} confirmed")

                    return True
                return False
    except Exception as e:
        logger.error(f"[FORMATION] Error confirming membership: {e}")
        return False


def start_formation(db, community_id: str) -> bool:
    """
    Start the formal LEG formation process.
    
    Args:
        db: Database module
        community_id: Community ID
    
    Returns:
        True if successful
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Check minimum members
                cur.execute("""
                    SELECT COUNT(*) as count FROM community_members
                    WHERE community_id = %s AND status = 'confirmed'
                """, (community_id,))
                
                count = cur.fetchone()['count']
                if count < FORMATION_CONFIG["min_community_size"]:
                    logger.warning(f"[FORMATION] Community {community_id} has only {count} members, need {FORMATION_CONFIG['min_community_size']}")
                    return False
                
                # Update status
                cur.execute("""
                    UPDATE communities
                    SET status = %s, formation_started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE community_id = %s
                """, (FormationStatus.FORMATION_STARTED.value, community_id))
                
                db.track_event("formation_started", None, {"community_id": community_id})
                logger.info(f"[FORMATION] Started formation for community {community_id}")
                return True
    except Exception as e:
        logger.error(f"[FORMATION] Error starting formation: {e}")
        return False


def _build_pdf(title: str, lines: List[str]) -> bytes:
    """Build a simple text PDF using reportlab. Returns PDF bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, h - 60, title)
    c.setFont("Helvetica", 10)
    y = h - 100
    for line in lines:
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = h - 60
        c.drawString(50, y, line)
        y -= 14
    c.save()
    return buf.getvalue()


def generate_documents(db, community_id: str) -> Optional[Dict]:
    """Generate contract PDFs, store them, optionally send to DeepSign."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT c.*, array_agg(
                        jsonb_build_object(
                            'building_id', cm.building_id,
                            'role', cm.role,
                            'status', cm.status,
                            'email', b.email,
                            'address', b.address
                        )
                    ) as members
                    FROM communities c
                    JOIN community_members cm ON c.community_id = cm.community_id
                    JOIN buildings b ON cm.building_id = b.building_id
                    WHERE c.community_id = %s
                    GROUP BY c.community_id
                """, (community_id,))

                community = cur.fetchone()
                if not community:
                    return None

                confirmed = [m for m in community['members'] if m['status'] == 'confirmed']
                name = community['name']
                now_iso = datetime.now().isoformat()

                # 1) Community agreement PDF
                agreement_lines = [
                    f"Gemeinschaftsvereinbarung: {name}",
                    f"Community ID: {community_id}",
                    f"Verteilmodell: {community['distribution_model']}",
                    f"Datum: {now_iso[:10]}",
                    "",
                    "Teilnehmer:",
                ]
                for m in confirmed:
                    agreement_lines.append(f"  - {m.get('address', m['building_id'])} ({m.get('email', '')})")
                agreement_lines += ["", "Unterschriften:", ""]
                for m in confirmed:
                    agreement_lines.append(f"_________________________  {m.get('address', m['building_id'])}")
                    agreement_lines.append("")

                agreement_pdf = _build_pdf("Gemeinschaftsvereinbarung LEG", agreement_lines)
                agreement_doc_id = str(uuid.uuid4())

                db.store_leg_document(community_id, "community_agreement", agreement_pdf, f"vereinbarung_{community_id[:8]}.pdf")

                # 2) Participant contracts
                participant_docs = []
                for m in confirmed:
                    lines = [
                        f"Teilnehmervertrag LEG: {name}",
                        f"Teilnehmer: {m.get('address', m['building_id'])}",
                        f"E-Mail: {m.get('email', '')}",
                        f"Rolle: {m.get('role', 'member')}",
                        f"Datum: {now_iso[:10]}",
                        "",
                        "Hiermit erklaert sich der Teilnehmer bereit, an der",
                        f"Lokalen Elektrizitaetsgemeinschaft '{name}' teilzunehmen.",
                        "",
                        "_________________________",
                        "Unterschrift",
                    ]
                    pdf = _build_pdf("Teilnehmervertrag LEG", lines)
                    db.store_leg_document(community_id, "participant_contract", pdf, f"vertrag_{m['building_id'][:8]}.pdf")
                    participant_docs.append({
                        "document_id": str(uuid.uuid4()),
                        "building_id": m['building_id'],
                        "template": "participant_contract",
                        "generated_at": now_iso,
                        "status": "pending_signature",
                    })

                # 3) DSO notification form
                dso_lines = [
                    f"Anmeldung Lokale Elektrizitaetsgemeinschaft",
                    f"Gemeinschaft: {name}",
                    f"Community ID: {community_id}",
                    f"Anzahl Teilnehmer: {len(confirmed)}",
                    f"Datum: {now_iso[:10]}",
                    "",
                    "Teilnehmer und Messpunkte:",
                ]
                for m in confirmed:
                    dso_lines.append(f"  - {m.get('address', m['building_id'])}")
                dso_pdf = _build_pdf("VNB-Anmeldeformular LEG", dso_lines)
                dso_doc_id = str(uuid.uuid4())
                db.store_leg_document(community_id, "dso_notification", dso_pdf, f"vnb_anmeldung_{community_id[:8]}.pdf")

                documents = {
                    "community_agreement": {
                        "document_id": agreement_doc_id,
                        "template": "community_agreement",
                        "generated_at": now_iso,
                        "status": "generated",
                        "signatures_required": len(confirmed),
                        "signatures_collected": 0,
                    },
                    "participant_contracts": participant_docs,
                    "dso_notification": {
                        "document_id": dso_doc_id,
                        "template": "dso_notification",
                        "generated_at": now_iso,
                        "status": "ready",
                        "recipient": CONTRACT_TEMPLATES["dso_notification"]["recipient"],
                    },
                }

                # Save document metadata
                cur.execute("""
                    INSERT INTO community_documents (community_id, documents, generated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (community_id) DO UPDATE SET
                        documents = EXCLUDED.documents,
                        generated_at = EXCLUDED.generated_at
                """, (community_id, documents))

                # DeepSign integration (gated behind env var)
                deepsign_key = os.getenv("DEEPSIGN_API_KEY", "")
                if deepsign_key:
                    try:
                        import deepsign_integration
                        signers = [{"name": m.get('address', m['building_id']), "email": m['email']}
                                   for m in confirmed if m.get('email')]

                        ds_id = deepsign_integration.upload_document(
                            agreement_pdf, f"vereinbarung_{community_id[:8]}.pdf",
                            f"Gemeinschaftsvereinbarung {name}")
                        deepsign_integration.request_signatures(ds_id, signers)

                        # Store deepsign_document_id so webhook can match
                        cur.execute("""
                            UPDATE leg_documents SET deepsign_document_id = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE community_id = %s AND doc_type = 'community_agreement'
                        """, (ds_id, community_id))

                        # Update status to signatures_pending
                        cur.execute("""
                            UPDATE communities SET status = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE community_id = %s
                        """, (FormationStatus.SIGNATURES_PENDING.value, community_id))
                        logger.info(f"[FORMATION] DeepSign signatures requested for {community_id}")
                    except Exception as ds_err:
                        logger.error(f"[FORMATION] DeepSign error: {ds_err}")
                        cur.execute("""
                            UPDATE communities SET status = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE community_id = %s
                        """, (FormationStatus.DOCUMENTS_GENERATED.value, community_id))
                else:
                    cur.execute("""
                        UPDATE communities SET status = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE community_id = %s
                    """, (FormationStatus.DOCUMENTS_GENERATED.value, community_id))

                logger.info(f"[FORMATION] Generated documents for community {community_id}")
                return documents
    except Exception as e:
        logger.error(f"[FORMATION] Error generating documents: {e}")
        return None


def send_dso_email(to_email: str, subject: str, body: str, attachments: List[Dict]) -> bool:
    """Send DSO notification email with PDF attachments.

    Uses the shared send_email path (AgentMail primary, SMTP fallback).
    Attachments logged but only sent via SMTP fallback (AgentMail has no
    attachment API); in dev mode (no credentials) returns True with log.

    Args:
        to_email: DSO contact email
        subject: Email subject
        body: Email body text
        attachments: list of {"filename": str, "data": bytes}

    Returns True on success.
    """
    try:
        from email_utils import EMAIL_ENABLED, SMTP_ENABLED, FROM_EMAIL, \
            SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

        if not EMAIL_ENABLED:
            att_names = [a['filename'] for a in attachments]
            logger.info(f"[DSO] (dev) Would send to {to_email}: {subject} attachments={att_names}")
            return True

        # Build MIME with attachments
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.application import MIMEApplication

        msg = MIMEMultipart()
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        for att in attachments:
            part = MIMEApplication(att['data'], Name=att['filename'])
            part['Content-Disposition'] = f'attachment; filename="{att["filename"]}"'
            msg.attach(part)

        if SMTP_ENABLED:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
            logger.info(f"[DSO] SMTP sent to {to_email}: {subject}")
            return True

        # AgentMail fallback (no attachment support): send body only
        from email_utils import send_email
        att_names = [a['filename'] for a in attachments]
        body_with_note = body + f"\n\n[Anhänge: {', '.join(att_names)}]"
        return send_email(to_email, subject, body_with_note)
    except Exception as e:
        logger.error(f"[DSO] Failed to send email to {to_email}: {e}")
        return False


def submit_to_dso(db, community_id: str) -> bool:
    """Submit DSO notification: pull docs, email DSO contact, update status."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Get community
                cur.execute("SELECT * FROM communities WHERE community_id = %s", (community_id,))
                community = cur.fetchone()
                if not community:
                    return False

                # Get leg documents
                docs = db.list_leg_documents(community_id)
                attachments = []
                for doc in docs:
                    if doc.get('doc_type') in ('community_agreement', 'dso_notification'):
                        pdf = db.get_leg_document_pdf(doc['id'])
                        if pdf:
                            attachments.append({"filename": doc.get('filename', 'document.pdf'), "data": pdf})

                # Get DSO contact from tenant config
                territory = None
                # Try to find territory from admin building's city_id
                if community.get('admin_building_id'):
                    cur.execute("SELECT city_id FROM buildings WHERE building_id = %s",
                                (community['admin_building_id'],))
                    brow = cur.fetchone()
                    if brow:
                        territory = brow.get('city_id')

                dso_email = None
                if territory:
                    tenant = db.get_tenant_by_territory(territory)
                    if tenant:
                        dso_email = tenant.get('dso_contact', '')

                name = community.get('name', community_id)
                subject = f"LEG-Anmeldung: {name}"
                body = (
                    f"Sehr geehrte Damen und Herren,\n\n"
                    f"hiermit melden wir die Lokale Elektrizitaetsgemeinschaft '{name}' an.\n"
                    f"Community ID: {community_id}\n\n"
                    f"Im Anhang finden Sie die Gemeinschaftsvereinbarung und das VNB-Anmeldeformular.\n\n"
                    f"Freundliche Gruesse\nOpenLEG"
                )

                sent = False
                if dso_email:
                    sent = send_dso_email(dso_email, subject, body, attachments)
                else:
                    # No DSO contact: log only (dev/staging)
                    logger.info(f"[DSO] No DSO contact for {community_id}, logging submission")
                    sent = True

                if not sent:
                    return False

                cur.execute("""
                    UPDATE communities
                    SET status = %s, dso_submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE community_id = %s
                """, (FormationStatus.DSO_SUBMITTED.value, community_id))

                db.track_event("dso_submitted", None, {"community_id": community_id})
                logger.info(f"[FORMATION] Submitted DSO notification for community {community_id}")
                return True
    except Exception as e:
        logger.error(f"[FORMATION] Error submitting to DSO: {e}")
        return False


def get_community_status(db, community_id: str) -> Optional[Dict]:
    """
    Get full status of a community formation.
    
    Args:
        db: Database module
        community_id: Community ID
    
    Returns:
        Community status dict or None
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        c.*,
                        array_agg(
                            jsonb_build_object(
                                'building_id', cm.building_id,
                                'role', cm.role,
                                'status', cm.status,
                                'email', b.email,
                                'address', b.address,
                                'confirmed_at', cm.confirmed_at
                            ) ORDER BY cm.joined_at
                        ) FILTER (WHERE cm.building_id IS NOT NULL) as members,
                        cd.documents as documents
                    FROM communities c
                    LEFT JOIN community_members cm ON c.community_id = cm.community_id
                    LEFT JOIN buildings b ON cm.building_id = b.building_id
                    LEFT JOIN community_documents cd ON c.community_id = cd.community_id
                    WHERE c.community_id = %s
                    GROUP BY c.community_id, cd.documents
                """, (community_id,))
                
                row = cur.fetchone()
                if not row:
                    return None
                
                # Calculate readiness score
                confirmed_count = sum(1 for m in row['members'] if m['status'] == 'confirmed')
                total_count = len(row['members'])
                
                readiness_score = 0
                if confirmed_count >= FORMATION_CONFIG["min_community_size"]:
                    readiness_score += 30
                if row['status'] in [FormationStatus.DOCUMENTS_GENERATED.value, FormationStatus.SIGNATURES_PENDING.value]:
                    readiness_score += 30
                if row['status'] == FormationStatus.DSO_SUBMITTED.value:
                    readiness_score += 20
                if row['status'] == FormationStatus.DSO_APPROVED.value:
                    readiness_score += 20
                
                return {
                    "community_id": row['community_id'],
                    "name": row['name'],
                    "status": row['status'],
                    "distribution_model": row['distribution_model'],
                    "admin_building_id": row['admin_building_id'],
                    "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                    "formation_started_at": row['formation_started_at'].isoformat() if row['formation_started_at'] else None,
                    "dso_submitted_at": row['dso_submitted_at'].isoformat() if row['dso_submitted_at'] else None,
                    "member_count": {
                        "total": total_count,
                        "confirmed": confirmed_count,
                        "invited": total_count - confirmed_count
                    },
                    "readiness_score": readiness_score,
                    "members": row['members'],
                    "documents": row['documents'],
                    "next_steps": _get_next_steps(row['status'], confirmed_count)
                }
    except Exception as e:
        logger.error(f"[FORMATION] Error getting community status: {e}")
        return None


def _get_next_steps(status: str, confirmed_count: int) -> List[str]:
    """Get recommended next steps based on status."""
    steps = []
    
    if status == FormationStatus.INTERESTED.value:
        if confirmed_count < FORMATION_CONFIG["min_community_size"]:
            steps.append(f"Invite at least {FORMATION_CONFIG['min_community_size'] - confirmed_count} more neighbors")
        else:
            steps.append("Start formation process")
    
    elif status == FormationStatus.FORMATION_STARTED.value:
        steps.append("Generate legal documents")
        steps.append("Review community agreement")
    
    elif status == FormationStatus.DOCUMENTS_GENERATED.value:
        steps.append("Collect signatures from all members")
        steps.append("Review participant contracts")
    
    elif status == FormationStatus.SIGNATURES_PENDING.value:
        steps.append("Submit DSO notification")
    
    elif status == FormationStatus.DSO_SUBMITTED.value:
        steps.append("Wait for DSO approval (up to 30 days)")
    
    elif status == FormationStatus.DSO_APPROVED.value:
        steps.append("Set activation date")
        steps.append("Configure billing")
    
    return steps


def get_user_communities(db, building_id: str) -> List[Dict]:
    """
    Get all communities a user is part of.
    
    Args:
        db: Database module
        building_id: Building ID
    
    Returns:
        List of community dicts
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        c.community_id,
                        c.name,
                        c.status,
                        c.distribution_model,
                        cm.role,
                        cm.status as member_status,
                        (SELECT COUNT(*) FROM community_members WHERE community_id = c.community_id) as member_count
                    FROM communities c
                    JOIN community_members cm ON c.community_id = cm.community_id
                    WHERE cm.building_id = %s
                    ORDER BY c.created_at DESC
                """, (building_id,))
                
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[FORMATION] Error getting user communities: {e}")
        return []


def get_formable_clusters(db, building_id: str, radius_meters: int = 150) -> List[Dict]:
    """
    Get clusters that are ready for formation (have enough members).
    
    Args:
        db: Database module
        building_id: Building ID to center search
        radius_meters: Search radius
    
    Returns:
        List of formable cluster dicts
    """
    try:
        # Get user's location
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT lat, lon FROM buildings WHERE building_id = %s
                """, (building_id,))
                
                user = cur.fetchone()
                if not user:
                    return []
                
                # Find nearby buildings not in communities
                cur.execute("""
                    SELECT 
                        b.building_id,
                        b.address,
                        b.email,
                        b.lat,
                        b.lon,
                        (6371000 * acos(
                            cos(radians(%s)) * cos(radians(b.lat)) *
                            cos(radians(b.lon) - radians(%s)) +
                            sin(radians(%s)) * sin(radians(b.lat))
                        )) as distance
                    FROM buildings b
                    WHERE b.verified = TRUE
                    AND b.building_id != %s
                    AND NOT EXISTS (
                        SELECT 1 FROM community_members cm
                        WHERE cm.building_id = b.building_id
                        AND cm.status IN ('confirmed', 'invited')
                    )
                    HAVING distance <= %s
                    ORDER BY distance
                """, (user['lat'], user['lon'], user['lat'], building_id, radius_meters))
                
                nearby = [dict(row) for row in cur.fetchall()]
                
                if len(nearby) >= FORMATION_CONFIG["min_community_size"] - 1:
                    return [{
                        "potential_members": len(nearby) + 1,  # +1 for user
                        "nearby_buildings": nearby[:10],  # Top 10
                        "radius_meters": radius_meters,
                        "ready_to_form": len(nearby) + 1 >= FORMATION_CONFIG["min_community_size"]
                    }]
                return []
    except Exception as e:
        logger.error(f"[FORMATION] Error getting formable clusters: {e}")
        return []


def calculate_municipality_business_case(
    bfs_number: int,
    num_legs: int = 5,
    avg_community_size: int = 10,
    avg_pv_kwp: float = 30,
    avg_consumption_kwh: float = 4500,
) -> Dict:
    """
    Calculate business case for a municipality's LEG program.
    Returns aggregate projections for multiple LEGs.
    """
    per_household = calculate_savings_estimate(
        avg_consumption_kwh, avg_pv_kwp, avg_community_size
    )
    annual_per_hh = per_household.get("annual_savings_chf", 0)
    total_households = num_legs * avg_community_size

    projections = []
    cumulative = 0
    for year in range(1, 11):
        year_savings = annual_per_hh * total_households * (1.02 ** (year - 1))
        cumulative += year_savings
        projections.append({
            "year": year,
            "annual_total_chf": round(year_savings, 2),
            "cumulative_chf": round(cumulative, 2),
        })

    co2_per_leg = avg_pv_kwp * 950 * 0.3 * 0.128  # kg CO2
    return {
        "bfs_number": bfs_number,
        "num_legs": num_legs,
        "total_households": total_households,
        "annual_savings_per_household": round(annual_per_hh, 2),
        "annual_total_savings": round(annual_per_hh * total_households, 2),
        "projections": projections,
        "co2_reduction_total_kg": round(co2_per_leg * num_legs, 1),
        "assumptions": per_household.get("assumptions", {}),
    }


def calculate_savings_estimate(
    consumption_kwh: float,
    pv_kwp: float,
    community_size: int,
    solar_kwh_per_kwp: int = 900
) -> Dict:
    """
    Calculate estimated savings for a household in a LEG.
    
    Args:
        consumption_kwh: Annual consumption in kWh
        pv_kwp: PV capacity in kWp
        community_size: Number of households in community
    
    Returns:
        Savings estimate dict
    """
    # Swiss energy prices (Rp/kWh)
    GRID_BUY_PRICE = 25.0  # Buying from grid
    GRID_SELL_PRICE = 6.0  # Selling to grid
    LEG_PRICE = 15.0       # LEG internal price
    
    # Estimate production (800-1050 kWh/kWp/year in Switzerland, varies by region)
    estimated_production = pv_kwp * solar_kwh_per_kwp if pv_kwp else 0
    
    # Simple model: share production within community
    if estimated_production > 0:
        # Producer scenario
        self_consumption = min(consumption_kwh, estimated_production * 0.3)
        leg_sales = min(estimated_production - self_consumption, consumption_kwh * (community_size - 1))
        grid_sales = estimated_production - self_consumption - leg_sales
        grid_purchase = max(0, consumption_kwh - self_consumption)
        
        # Revenue/cost
        leg_revenue = leg_sales * LEG_PRICE / 100  # Convert Rp to CHF
        grid_revenue = grid_sales * GRID_SELL_PRICE / 100
        grid_cost = grid_purchase * GRID_BUY_PRICE / 100
        
        net_cost = grid_cost - leg_revenue - grid_revenue
        
        # Without LEG
        without_leg_cost = (consumption_kwh * GRID_BUY_PRICE / 100) - (estimated_production * GRID_SELL_PRICE / 100)
        
        annual_savings = without_leg_cost - net_cost
    else:
        # Consumer scenario
        # Assume community provides 30% of consumption
        leg_purchase = consumption_kwh * 0.3
        grid_purchase = consumption_kwh * 0.7
        
        with_leg_cost = (leg_purchase * LEG_PRICE / 100) + (grid_purchase * GRID_BUY_PRICE / 100)
        without_leg_cost = consumption_kwh * GRID_BUY_PRICE / 100
        
        annual_savings = without_leg_cost - with_leg_cost
    
    return {
        "annual_savings_chf": round(annual_savings, 2),
        "monthly_savings_chf": round(annual_savings / 12, 2),
        "five_year_savings_chf": round(annual_savings * 5, 2),
        "assumptions": {
            "grid_buy_price_rp": GRID_BUY_PRICE,
            "grid_sell_price_rp": GRID_SELL_PRICE,
            "leg_price_rp": LEG_PRICE,
            "community_size": community_size
        }
    }

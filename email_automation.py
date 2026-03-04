"""
Email Automation for OpenLEG
Handles scheduled email sequences for user nurturing.
"""
import os
import time
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, List

logger = logging.getLogger(__name__)

from flask import render_template

import database as db
from email_utils import send_email, EMAIL_ENABLED

APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5003').rstrip('/')

def get_email_sequence(platform_name="OpenLEG"):
    """Return email sequence with dynamic platform name in subjects."""
    return {
        "day_0_welcome": {
            "delay_days": 0,
            "subject": f"Willkommen bei {platform_name}! Ihre Nachbarn warten",
            "template": "emails/day_0_welcome.html",
        },
        "day_3_smartmeter": {
            "delay_days": 3,
            "subject": "Schnelle Frage: Haben Sie einen Smart Meter?",
            "template": "emails/day_3_smartmeter.html",
        },
        "day_7_consumption": {
            "delay_days": 7,
            "subject": "Optimieren Sie Ihr LEG-Matching",
            "template": "emails/day_7_consumption.html",
        },
        "day_14_formation": {
            "delay_days": 14,
            "subject": "Ihre LEG-Gemeinschaft kann starten",
            "template": "emails/day_14_formation.html",
        },
    }

# Standalone trigger templates (not part of drip sequence)
TRIGGER_TEMPLATES = {
    "formation_nudge": {
        "subject": "Ihre LEG-Gründung wartet",
        "template": "emails/formation_nudge.html",
    },
}

# Default sequence (backward compatible)
EMAIL_SEQUENCE = get_email_sequence()


def schedule_sequence_for_user(building_id: str, email: str):
    """Schedule the full email sequence for a newly registered user."""
    now = time.time()
    scheduled = 0
    for key, config in EMAIL_SEQUENCE.items():
        send_at = now + (config["delay_days"] * 86400)
        if db.schedule_email(building_id, email, key, send_at):
            scheduled += 1
    logger.info(f"[EMAIL_AUTO] Scheduled {scheduled} emails for {building_id}")
    return scheduled


def _get_tenant_for_building(building_id: str) -> dict:
    """Load tenant config for a building's city_id."""
    from tenant import get_tenant_config, DEFAULT_TENANT
    building = db.get_building(building_id)
    if building:
        city_id = building.get('city_id', 'baden')
        return get_tenant_config(city_id, db=db)
    return DEFAULT_TENANT.copy()


def process_email_queue(app=None):
    """Process pending emails. Call from cron endpoint."""
    pending = db.get_pending_emails(limit=50)
    sent = 0
    failed = 0

    for item in pending:
        email_id = item['id']
        template_key = item['template_key']

        # Resolve tenant for this building
        tenant = _get_tenant_for_building(item['building_id'])
        sequence = get_email_sequence(tenant.get('platform_name', 'OpenLEG'))
        config = sequence.get(template_key)
        if not config:
            db.mark_email_failed(email_id, f"Unknown template: {template_key}")
            failed += 1
            continue

        # Build unsubscribe URL
        unsubscribe_url = f"{APP_BASE_URL}/unsubscribe"

        # Get neighbor count for personalization
        neighbor_count = 0
        if item.get('lat') and item.get('lon'):
            neighbor_count = db.get_neighbor_count_near(
                float(item['lat']), float(item['lon']),
                city_id=tenant.get('territory')
            )

        # Get referral code
        referral_code = db.get_referral_code(item['building_id']) or ''
        referral_link = f"{APP_BASE_URL}/?ref={referral_code}" if referral_code else ''

        # Render template with tenant context
        try:
            if app:
                with app.app_context():
                    html_body = render_template(
                        config["template"],
                        email=item['email'],
                        address=item.get('address', ''),
                        neighbor_count=neighbor_count,
                        unsubscribe_url=unsubscribe_url,
                        referral_link=referral_link,
                        site_url=APP_BASE_URL,
                        dashboard_url=f"{APP_BASE_URL}/dashboard?bid={item['building_id']}",
                        tenant=tenant,
                        platform_name=tenant.get('platform_name', 'OpenLEG'),
                        city_name=tenant.get('city_name', 'Baden'),
                        primary_color=tenant.get('primary_color', '#c7021a'),
                        contact_email=tenant.get('contact_email', 'hallo@openleg.ch'),
                        utility_name=tenant.get('utility_name', 'Regionalwerke Baden'),
                    )
            else:
                pname = tenant.get('platform_name', 'OpenLEG')
                html_body = f"<p>{pname}: {config['subject']}</p>"
        except Exception as e:
            logger.error(f"[EMAIL_AUTO] Template render error for {template_key}: {e}")
            db.mark_email_failed(email_id, str(e))
            failed += 1
            continue

        # Send email
        success = _send_email(item['email'], config['subject'], html_body)
        if success:
            db.mark_email_sent(email_id)
            sent += 1
        else:
            db.mark_email_failed(email_id, "SMTP delivery failed")
            failed += 1

    logger.info(f"[EMAIL_AUTO] Processed queue: {sent} sent, {failed} failed, {len(pending)} total")
    return {"sent": sent, "failed": failed, "total": len(pending)}


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send a single email via SMTP."""
    return send_email(to_email, subject, html_body, html=True)


def send_formation_nudge(community_id: str, community_name: str, member_emails: list, app=None):
    """Send formation nudge email to all confirmed members of a community."""
    config = TRIGGER_TEMPLATES["formation_nudge"]
    sent = 0
    for email in member_emails:
        try:
            if app:
                with app.app_context():
                    html_body = render_template(
                        config["template"],
                        community_name=community_name,
                        email=email,
                        days_stuck=14,
                        site_url=APP_BASE_URL,
                    )
            else:
                html_body = f"<p>{config['subject']}: {community_name}</p>"
            if _send_email(email, config["subject"], html_body):
                sent += 1
        except Exception as e:
            logger.error(f"[EMAIL_AUTO] Nudge send error for {email}: {e}")
    logger.info(f"[EMAIL_AUTO] Formation nudge sent to {sent}/{len(member_emails)} for {community_id}")
    return sent


def check_formation_ready_communities(min_members: int = 3, nudge_cooldown_days: int = 7):
    """Find communities with enough confirmed members but no recent nudge."""
    try:
        communities = db.get_active_communities_for_nudge(
            min_members=min_members,
            cooldown_days=nudge_cooldown_days
        )
        return communities
    except Exception as e:
        logger.error(f"[EMAIL_AUTO] Error checking formation-ready communities: {e}")
        return []


def monitor_formation_pipeline():
    """Aggregate formation pipeline health: status counts, stuck communities, totals."""
    by_status = db.get_formation_pipeline_stats()
    stuck = check_formation_ready_communities()
    total = sum(by_status.values())
    return {
        'by_status': by_status,
        'stuck': stuck,
        'total_communities': total,
        'healthy': len(stuck) == 0,
    }


def render_outreach_email(municipality_profile, app=None):
    """Render Gemeinde outreach email from municipality profile data. No sending."""
    name = municipality_profile.get('name', '')
    kanton = municipality_profile.get('kanton', '')
    bfs = municipality_profile.get('bfs_number', '')
    score = municipality_profile.get('energy_transition_score', 0)
    gap = municipality_profile.get('leg_value_gap_chf', 0)
    subdomain = name.lower().replace(' ', '-') if name else ''
    profil_url = f"{APP_BASE_URL}/gemeinde/{bfs}/profil"
    claim_url = f"{APP_BASE_URL}/gemeinde/onboarding?subdomain={subdomain}"

    ctx = dict(
        gemeinde_name=name, kanton=kanton,
        energy_transition_score=score, leg_value_gap_chf=gap,
        subdomain=subdomain, profil_url=profil_url, claim_url=claim_url,
    )

    if app:
        with app.app_context():
            return render_template('emails/gemeinde_outreach.html', **ctx)
    return render_template('emails/gemeinde_outreach.html', **ctx)


# === Municipality Outreach Phase 2 ===

OUTREACH_TEMPLATES = {
    0: {'subject': 'Freie Infrastruktur für Ihre Gemeinde', 'template': 'emails/gemeinde_outreach.html'},
    1: {'subject': 'Haben Sie unsere Nachricht gesehen?', 'template': 'emails/gemeinde_followup_1.html'},
    2: {'subject': 'Letzte Erinnerung: Lokale Stromgemeinschaft', 'template': 'emails/gemeinde_followup_2.html'},
}


def score_outreach_candidates(profiles: list, already_contacted: set) -> list:
    """Score municipality profiles for outreach priority.
    Factors: energy_transition_score (40%), leg_value_gap_chf (30%), population (30%).
    Exclude already_contacted emails. Returns sorted list with 'outreach_score' added."""
    candidates = []
    for p in profiles:
        email = p.get('contact_email', '')
        if not email or email in already_contacted:
            continue
        score = (
            float(p.get('energy_transition_score', 0)) * 0.4
            + float(p.get('leg_value_gap_chf', 0)) * 0.3
            + float(p.get('population', 0)) / 1000.0 * 0.3
        )
        entry = dict(p)
        entry['outreach_score'] = round(score, 2)
        candidates.append(entry)
    candidates.sort(key=lambda x: x['outreach_score'], reverse=True)
    return candidates


def schedule_outreach_batch(limit: int = 10) -> int:
    """Pick top N candidates, schedule initial outreach. Returns count scheduled."""
    profiles = db.get_all_municipality_profiles()
    vnb_records = db.get_all_vnb_research()

    # Build email lookup from VNB research (municipality contact_email via VNB)
    vnb_email_map = {}
    for v in vnb_records:
        email = v.get('contact_email', '')
        if not email:
            continue
        for bfs in (v.get('bfs_numbers') or []):
            vnb_email_map[bfs] = email

    # Enrich profiles with contact_email from VNB
    for p in profiles:
        if not p.get('contact_email'):
            p['contact_email'] = vnb_email_map.get(p.get('bfs_number'), '')

    # Get already contacted emails
    history = db.get_municipality_outreach_history()
    already_contacted = {h['contact_email'] for h in history if h.get('contact_email')}

    candidates = score_outreach_candidates(profiles, already_contacted)
    scheduled = 0
    for c in candidates[:limit]:
        result = db.schedule_municipality_outreach(
            name=c.get('name', ''),
            bfs=c.get('bfs_number'),
            kanton=c.get('kanton', ''),
            email=c['contact_email'],
            email_type='initial',
            followup_number=0,
        )
        if result:
            scheduled += 1
    logger.info(f"[OUTREACH] Scheduled {scheduled} new municipality outreach emails")
    return scheduled


def process_municipality_outreach(app=None):
    """Process pending municipality outreach queue. Returns {'sent': N, 'failed': N}."""
    pending = db.get_pending_municipality_outreach(limit=50)
    sent = 0
    failed = 0

    for item in pending:
        outreach_id = item['id']
        name = item['municipality_name']
        bfs = item.get('bfs_number', '')
        kanton = item.get('kanton', '')
        followup_number = item.get('followup_number', 0)

        tpl_config = OUTREACH_TEMPLATES.get(followup_number, OUTREACH_TEMPLATES[0])
        subdomain = name.lower().replace(' ', '-') if name else ''
        profil_url = f"{APP_BASE_URL}/gemeinde/{bfs}/profil"
        claim_url = f"{APP_BASE_URL}/gemeinde/onboarding?subdomain={subdomain}"

        ctx = dict(
            gemeinde_name=name, kanton=kanton,
            energy_transition_score=0, leg_value_gap_chf=0,
            subdomain=subdomain, profil_url=profil_url, claim_url=claim_url,
        )

        try:
            if app:
                with app.app_context():
                    html_body = render_template(tpl_config['template'], **ctx)
            else:
                html_body = f"<p>OpenLEG: {tpl_config['subject']} ({name})</p>"
        except Exception as e:
            logger.error(f"[OUTREACH] Template render error for {name}: {e}")
            db.mark_municipality_outreach_failed(outreach_id, str(e))
            failed += 1
            continue

        success = _send_email(item['contact_email'], tpl_config['subject'], html_body)
        if success:
            db.mark_municipality_outreach_sent(outreach_id)
            sent += 1
        else:
            db.mark_municipality_outreach_failed(outreach_id, 'SMTP delivery failed')
            failed += 1

    if sent > 0:
        _notify_outreach_sent('batch', 'outreach', sent)

    logger.info(f"[OUTREACH] Processed: {sent} sent, {failed} failed")
    return {'sent': sent, 'failed': failed}


def schedule_municipality_followups(followup_number: int = 1, days_after: int = 7) -> int:
    """Find sent outreach with no response after N days, schedule follow-up."""
    needing = db.get_sent_outreach_needing_followup(followup_number, days_after)
    scheduled = 0
    for item in needing:
        result = db.schedule_municipality_outreach(
            name=item['municipality_name'],
            bfs=item.get('bfs_number'),
            kanton=item.get('kanton', ''),
            email=item['contact_email'],
            email_type='followup',
            followup_number=followup_number,
            scheduled_at=datetime.now(timezone.utc),
        )
        if result:
            scheduled += 1
    if scheduled > 0:
        logger.info(f"[OUTREACH] Scheduled {scheduled} follow-up #{followup_number} emails")
    return scheduled


def _notify_outreach_sent(municipality_name: str, email_type: str, count: int):
    """Send Telegram FYI about outreach sends. Non-blocking."""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not bot_token or not chat_id:
        return
    text = f"📬 *Outreach:* {count}x {email_type} ({municipality_name})"

    def _send():
        try:
            import urllib.request
            payload = json.dumps({
                "chat_id": chat_id, "text": text,
                "parse_mode": "Markdown", "disable_web_page_preview": True
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=payload, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"[Telegram] outreach notify failed: {e}")

    threading.Thread(target=_send, daemon=True).start()

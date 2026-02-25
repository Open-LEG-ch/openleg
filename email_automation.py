"""
Email Automation for OpenLEG
Handles scheduled email sequences for user nurturing.
"""
import os
import time
import logging
from typing import Optional

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

"""Shared email sending for OpenLEG. AgentMail primary, SMTP fallback."""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv('SMTP_HOST', 'mail.infomaniak.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'hallo@openleg.ch')
SMTP_ENABLED = bool(SMTP_USER and SMTP_PASSWORD)

# AgentMail
AGENTMAIL_API_KEY = os.getenv('AGENTMAIL_API_KEY', '').strip()
AGENTMAIL_DOMAIN = os.getenv('AGENTMAIL_DOMAIN', 'agentmail.to')
USE_AGENTMAIL = bool(AGENTMAIL_API_KEY)

# Backward compat
EMAIL_ENABLED = USE_AGENTMAIL or SMTP_ENABLED

# Transactional inbox (lazy init)
_transactional_inbox = None


def _get_transactional_inbox():
    """Ensure transactional inbox exists, return inbox_id."""
    global _transactional_inbox
    if _transactional_inbox:
        return _transactional_inbox
    try:
        import agentmail_client

        inbox_id = agentmail_client.ensure_inbox('hallo', AGENTMAIL_DOMAIN)
        if inbox_id:
            _transactional_inbox = inbox_id
        return inbox_id
    except Exception as e:
        logger.error(f'[EMAIL] Failed to get transactional inbox: {e}')
        return None


def _send_via_agentmail(to_email, subject, body, html=False):
    """Send via AgentMail API."""
    inbox_id = _get_transactional_inbox()
    if not inbox_id:
        return False
    try:
        import agentmail_client

        text_body = None if html else body
        html_body = body if html else None
        msg_id = agentmail_client.send_message(
            inbox_id=inbox_id, to=to_email, subject=subject, text=text_body or body, html=html_body
        )
        return msg_id is not None
    except Exception as e:
        logger.error(f'[EMAIL] AgentMail send failed: {e}')
        return False


def _send_via_smtp(to_email, subject, body, html=False, from_email=None):
    """Send via SMTP (original path)."""
    if not SMTP_ENABLED:
        return False
    try:
        sender = from_email or FROM_EMAIL
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html' if html else 'plain', 'utf-8'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info(f'[EMAIL] SMTP sent to {to_email}: {subject}')
        return True
    except Exception as e:
        logger.error(f'[EMAIL] SMTP failed to send to {to_email}: {e}')
        return False


def send_email(to_email, subject, body, html=False, from_email=None):
    """Send email. AgentMail primary, SMTP fallback.

    Signature unchanged for backward compatibility.
    """
    if not EMAIL_ENABLED:
        logger.info(f'[EMAIL] (dev) Would send to {to_email}: {subject}')
        return True

    # Try AgentMail first
    if USE_AGENTMAIL:
        result = _send_via_agentmail(to_email, subject, body, html)
        if result:
            return True
        logger.warning('[EMAIL] AgentMail failed, falling back to SMTP')

    # SMTP fallback
    return _send_via_smtp(to_email, subject, body, html, from_email)

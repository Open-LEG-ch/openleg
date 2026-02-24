"""Shared SMTP email sending for OpenLEG."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv('SMTP_HOST', 'mail.infomaniak.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'hallo@openleg.ch')
EMAIL_ENABLED = bool(SMTP_USER and SMTP_PASSWORD)


def send_email(to_email, subject, body, html=False, from_email=None):
    if not EMAIL_ENABLED:
        logger.info(f"[EMAIL] (dev) Would send to {to_email}: {subject}")
        return True
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
        logger.info(f"[EMAIL] Sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False

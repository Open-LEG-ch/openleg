"""AgentMail SDK wrapper for OpenLEG."""

import logging
import os

logger = logging.getLogger(__name__)

AGENTMAIL_API_KEY = os.getenv('AGENTMAIL_API_KEY', '').strip()
AGENTMAIL_DOMAIN = os.getenv('AGENTMAIL_DOMAIN', 'mail.openleg.ch')

_client = None


def get_client():
    """Return cached AgentMail client singleton. None if no API key."""
    global _client
    if not AGENTMAIL_API_KEY:
        return None
    if _client is None:
        from agentmail import AgentMail

        _client = AgentMail(api_key=AGENTMAIL_API_KEY)
    return _client


def ensure_inbox(username, domain=None):
    """Create inbox if not exists, return inbox_id (email address).

    Looks up existing inboxes first to avoid duplicates.
    Returns None if AgentMail not configured.
    """
    client = get_client()
    if not client:
        return None
    domain = domain or AGENTMAIL_DOMAIN
    target = f'{username}@{domain}'
    try:
        existing = client.inboxes.list(limit=100)
        for inbox in existing.items if hasattr(existing, 'items') else []:
            if inbox.inbox_id == target:
                return target
        from agentmail.inboxes.types import CreateInboxRequest

        inbox = client.inboxes.create(
            request=CreateInboxRequest(username=username, domain=domain, display_name=f'OpenLEG {username}')
        )
        logger.info(f'[AgentMail] Created inbox: {inbox.inbox_id}')
        return inbox.inbox_id
    except Exception as e:
        err_str = str(e)
        if 'AlreadyExists' in err_str or 'IsTaken' in err_str or 'already exists' in err_str.lower():
            logger.info(f'[AgentMail] Inbox {target} already exists')
            return target
        logger.error(f'[AgentMail] Failed to ensure inbox {target}: {e}')
        return None


def send_message(inbox_id, to, subject, text, html=None):
    """Send email via AgentMail. Returns message_id or None."""
    client = get_client()
    if not client:
        return None
    try:
        kwargs = dict(to=to, subject=subject, text=text)
        if html:
            kwargs['html'] = html
        resp = client.inboxes.messages.send(inbox_id=inbox_id, **kwargs)
        msg_id = getattr(resp, 'message_id', None) or getattr(resp, 'id', None)
        logger.info(f'[AgentMail] Sent to {to}: {subject} (id={msg_id})')
        return msg_id
    except Exception as e:
        logger.error(f'[AgentMail] Failed to send to {to}: {e}')
        return None


def list_messages(inbox_id, limit=50):
    """List recent messages for an inbox. Returns list of dicts."""
    client = get_client()
    if not client:
        return []
    try:
        resp = client.inboxes.messages.list(inbox_id=inbox_id, limit=limit)
        items = resp.items if hasattr(resp, 'items') else []
        return [
            {
                'id': getattr(m, 'id', getattr(m, 'message_id', None)),
                'from': getattr(m, 'from_', getattr(m, 'sender', None)),
                'to': getattr(m, 'to', None),
                'subject': getattr(m, 'subject', None),
                'created_at': str(getattr(m, 'created_at', '')),
            }
            for m in items
        ]
    except Exception as e:
        logger.error(f'[AgentMail] Failed to list messages for {inbox_id}: {e}')
        return []

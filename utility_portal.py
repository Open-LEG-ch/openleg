"""
OpenLEG Utility Portal Blueprint.
Self-service portal for energy utilities (VNB/EVU) to manage their LEG platform.
Routes: /utility/*
"""
import os
import uuid
import hashlib
import secrets
import logging
from functools import wraps
from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session, g

import database as db
import security_utils
from email_utils import send_email

logger = logging.getLogger(__name__)

utility_bp = Blueprint('utility', __name__, url_prefix='/utility')

SITE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5003').rstrip('/')
MAGIC_LINK_TTL = 900  # 15 minutes


def _get_current_client():
    """Get current logged-in utility client from session."""
    client_id = session.get('utility_client_id')
    if not client_id:
        return None
    return db.get_utility_client(client_id)


def require_utility_auth(f):
    """Require authenticated utility client session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        client = _get_current_client()
        if not client:
            return redirect(url_for('utility.login_page'))
        g.utility_client = client
        return f(*args, **kwargs)
    return decorated


# --- Registration ---

@utility_bp.route('/register', methods=['GET'])
def register_page():
    return render_template('utility/register.html',
                           site_url=SITE_URL,
                           tenant=getattr(g, 'tenant', {}))


@utility_bp.route('/register', methods=['POST'])
def register():
    data = request.json or request.form.to_dict()

    company_name = (data.get('company_name') or '').strip()
    contact_name = (data.get('contact_name') or '').strip()
    contact_email = (data.get('contact_email') or '').strip()
    contact_phone = (data.get('contact_phone') or '').strip()
    vnb_name = (data.get('vnb_name') or '').strip()
    kanton = (data.get('kanton') or '').strip().upper()[:2]

    if not company_name or not contact_email:
        return jsonify({"error": "Firmenname und E-Mail sind erforderlich."}), 400

    is_valid, normalized, error = security_utils.validate_email_address(contact_email)
    if not is_valid:
        return jsonify({"error": error}), 400
    contact_email = normalized

    # Check if already registered
    existing = db.get_utility_client_by_email(contact_email)
    if existing:
        return jsonify({"error": "Diese E-Mail ist bereits registriert. Bitte einloggen."}), 409

    if contact_phone:
        is_valid_phone, normalized_phone, phone_error = security_utils.validate_phone(contact_phone)
        if is_valid_phone:
            contact_phone = normalized_phone

    population = None
    pop_raw = data.get('population')
    if pop_raw:
        try:
            population = int(pop_raw)
        except (ValueError, TypeError):
            pass

    client_id = str(uuid.uuid4())

    db.save_utility_client(
        client_id=client_id,
        company_name=company_name,
        contact_email=contact_email,
        contact_name=contact_name,
        contact_phone=contact_phone,
        vnb_name=vnb_name,
        population=population,
        kanton=kanton,
    )

    db.track_event('utility_registered', None, {
        'client_id': client_id,
        'company': company_name,
        'kanton': kanton
    })

    # Send magic link for first login
    _send_magic_link(client_id, contact_email)

    if request.is_json:
        return jsonify({
            "success": True,
            "message": "Registrierung erfolgreich. Login-Link per E-Mail gesendet."
        })
    return redirect(url_for('utility.login_page', registered=1))


# --- Login (Magic Link) ---

@utility_bp.route('/login', methods=['GET'])
def login_page():
    registered = request.args.get('registered')
    token = request.args.get('token')

    # Handle magic link callback
    if token:
        client = db.get_utility_client_by_magic_token(token)
        if client:
            db.clear_utility_magic_token(client['client_id'])
            session['utility_client_id'] = client['client_id']
            session.permanent = True
            if client['status'] == 'pending':
                db.update_utility_client_status(client['client_id'], 'active')
            return redirect(url_for('utility.dashboard'))
        return render_template('utility/login.html',
                               error="Ungültiger oder abgelaufener Login-Link.",
                               site_url=SITE_URL,
                               tenant=getattr(g, 'tenant', {}))

    return render_template('utility/login.html',
                           registered=registered,
                           site_url=SITE_URL,
                           tenant=getattr(g, 'tenant', {}))


@utility_bp.route('/login', methods=['POST'])
def login_submit():
    data = request.json or request.form.to_dict()
    email = (data.get('email') or '').strip()

    is_valid, normalized, error = security_utils.validate_email_address(email)
    if not is_valid:
        return jsonify({"error": error}), 400

    client = db.get_utility_client_by_email(normalized)
    if not client:
        # Don't reveal whether email exists
        return jsonify({"success": True, "message": "Falls registriert, erhalten Sie einen Login-Link per E-Mail."})

    _send_magic_link(client['client_id'], normalized)
    return jsonify({"success": True, "message": "Login-Link per E-Mail gesendet."})


@utility_bp.route('/logout')
def logout():
    session.pop('utility_client_id', None)
    return redirect(url_for('utility.login_page'))


# --- Dashboard ---

@utility_bp.route('/dashboard')
@require_utility_auth
def dashboard():
    client = g.utility_client
    return render_template('utility/dashboard.html',
                           client=client,
                           site_url=SITE_URL,
                           tenant=getattr(g, 'tenant', {}))


# --- API Key Management ---

@utility_bp.route('/api-key', methods=['POST'])
@require_utility_auth
def generate_api_key():
    """Generate a new API key for the utility client."""
    client = g.utility_client
    raw_key = f"oleg_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    db.update_utility_client_api_key(client['client_id'], key_hash)
    db.track_event('utility_api_key_generated', None, {'client_id': client['client_id']})

    return jsonify({
        "success": True,
        "api_key": raw_key,
        "message": "API-Schlüssel generiert. Bitte sicher aufbewahren, er wird nur einmal angezeigt."
    })


# --- Settings ---

@utility_bp.route('/settings', methods=['GET'])
@require_utility_auth
def settings_page():
    client = g.utility_client
    return render_template('utility/settings.html',
                           client=client,
                           site_url=SITE_URL,
                           tenant=getattr(g, 'tenant', {}))


# --- Admin: list all utility clients ---

@utility_bp.route('/admin/clients')
def admin_clients():
    admin_token = os.getenv('ADMIN_TOKEN', '').strip()
    if not admin_token:
        return jsonify({"error": "not found"}), 404
    token = request.headers.get('X-Admin-Token') or request.args.get('token') or ''
    if token != admin_token:
        return jsonify({"error": "forbidden"}), 403

    status_filter = request.args.get('status')
    clients = db.get_all_utility_clients(status=status_filter)
    stats = db.get_utility_client_stats()
    return jsonify({"clients": clients, "stats": stats})


# --- Helpers ---

def _send_magic_link(client_id: str, email: str):
    """Generate and send a magic login link."""
    token = secrets.token_urlsafe(48)
    db.set_utility_magic_token(client_id, token, MAGIC_LINK_TTL)

    login_url = f"{SITE_URL}/utility/login?token={token}"
    subject = "OpenLEG: Ihr Login-Link"
    body = (
        f"Guten Tag,\n\n"
        f"Klicken Sie auf den folgenden Link, um sich bei OpenLEG anzumelden:\n\n"
        f"{login_url}\n\n"
        f"Dieser Link ist {MAGIC_LINK_TTL // 60} Minuten gültig.\n\n"
        f"Ihr OpenLEG-Team"
    )
    send_email(email, subject, body)
    logger.info(f"[UTILITY] Magic link sent to {email}")

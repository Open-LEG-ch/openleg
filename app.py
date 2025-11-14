import os
import time
import uuid
import math
import hashlib
import threading
import logging
from datetime import timedelta
from flask import Flask, request, jsonify, render_template, abort, Response
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- Token Persistence ---
import token_persistence

try:
    from scipy.spatial import ConvexHull
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    print("[WARNUNG] scipy nicht verfügbar, verwende einfache Polygon-Generierung")

# --- Security imports ---
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    from flask_talisman import Talisman
    HAS_SECURITY_LIBS = True
except ImportError:
    HAS_SECURITY_LIBS = False
    print("[WARNUNG] Flask-Limiter oder Flask-Talisman nicht verfügbar, Security Features deaktiviert")

# --- Email imports ---
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    HAS_SENDGRID = True
except ImportError:
    HAS_SENDGRID = False
    print("[WARNUNG] SendGrid nicht verfügbar, E-Mails werden nur in Logs ausgegeben")

# --- Import unserer ML- und Geo-Logik ---
import data_enricher
import ml_models
import security_utils

# --- Logging Setup ---
# Simplified logging for Railway (no file logging)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- App-Konfiguration ---
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Security Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(32).hex())
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'False') == 'True'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=int(os.getenv('PERMANENT_SESSION_LIFETIME', 3600)))
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max request size

# --- Basis-URL für Bestätigungslinks ---
APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5003')
SITE_URL = APP_BASE_URL.rstrip('/')
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# --- Email Configuration ---
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY', '')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'noreply@badenleg.ch')
EMAIL_ENABLED = HAS_SENDGRID and SENDGRID_API_KEY

# --- Rate Limiting & Security (Minimal for Railway) ---
if HAS_SECURITY_LIBS:
    # Simplified rate limiting
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["500 per hour"],
        storage_uri='memory://',
        strategy="fixed-window"
    )
    
    # Minimal Talisman setup for Railway
    # Permissive CSP to allow Leaflet, Tailwind and map tiles
    Talisman(
        app,
        force_https=False,
        content_security_policy={
            'default-src': "'self'",
            'script-src': ["'self'", "'unsafe-inline'", "https://cdn.tailwindcss.com", "https://unpkg.com"],
            'style-src': ["'self'", "'unsafe-inline'", "https://unpkg.com"],
            'img-src': ["'self'", "data:", "https:", "http:"],
            'font-src': ["'self'", "data:"],
            'connect-src': ["'self'"]
        },
        content_security_policy_nonce_in=None
    )
    
    logger.info("Basic security features aktiviert")
else:
    limiter = None
    logger.warning("Security features deaktiviert")

# --- Security Helpers ---
def log_security_event(event_type, details, level='INFO'):
    """Log security-relevant events"""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    log_message = f"[SECURITY] {event_type} | IP: {ip} | {details}"
    if level == 'WARNING':
        logger.warning(log_message)
    elif level == 'ERROR':
        logger.error(log_message)
    else:
        logger.info(log_message)


@app.after_request
def apply_basic_security_headers(response):
    """Apply minimal security headers"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


# --- Simulierte In-Memory-Datenbank ---
DB_BUILDINGS = {} # Vollständig registrierte Gebäude
DB_INTEREST_POOL = {} # Anonyme Interessenten (Schlüssel: building_id)
DB_VERIFICATION_TOKENS = {} # Tokens für Verifizierungslinks
DB_UNSUBSCRIBE_TOKENS = {} # Tokens für Abmeldelinks
CLUSTER_CONTACT_STATE = {} # cluster_id -> Set der zuletzt benachrichtigten Gebäude

# --- NEU: Cache für ML-Ergebnisse ---
# Hält das Ergebnis des letzten langsamen ML-Laufs
DB_CLUSTERS = {} # z.B. {'building_abc': 1, 'building_xyz': 1}
DB_CLUSTER_INFO = {} # z.B. {1: {'autarky_percent': 75.2, ...}}
db_lock = threading.Lock() # Verhindert Race Conditions

ANONYMITY_RADIUS_METERS = 120  # Radius für Karten-Darstellung zur Wahrung der Privatsphäre

# Lade Tokens aus persistenter Speicherung
print(f"[INIT] Lade Tokens aus persistenter Speicherung...")
try:
    DB_VERIFICATION_TOKENS, DB_UNSUBSCRIBE_TOKENS, _token_created_at, _token_history = token_persistence.load_tokens()
except Exception as e:
    print(f"[INIT] Fehler beim Laden der Tokens: {e}")
    DB_VERIFICATION_TOKENS = {}
    DB_UNSUBSCRIBE_TOKENS = {}
    _token_created_at = {}
    _token_history = {}

print(f"[INIT] Leere Datenbank und ML-Cache initialisiert.")


# --- Kernfunktionen ---

def get_all_known_profiles():
    """Hilfsfunktion: Sammelt alle Profile aus DB und Pool."""
    all_profiles = []
    with db_lock:
        for building in DB_BUILDINGS.values():
            all_profiles.append(building['profile'])
        for building in DB_INTEREST_POOL.values():
            all_profiles.append(building['profile'])
    return all_profiles

def jitter_coordinates(lat, lon, radius_meters=ANONYMITY_RADIUS_METERS, seed=None):
    """
    Verschleiert die Koordinaten, indem ein zufälliger Offset innerhalb des angegebenen Radius
    angewendet wird. Der Offset ist deterministisch pro seed, damit Marker nicht bei jedem Reload
    springen.
    """
    if lat is None or lon is None or radius_meters <= 0:
        return lat, lon

    # Seed deterministisch aus building_id ableiten
    if seed is not None:
        if not isinstance(seed, str):
            seed = str(seed)
        seed_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        seed_value = int(seed_hash, 16)
    else:
        seed_value = None

    rng = np.random.default_rng(seed_value)
    # Gleichverteilung über Kreisfläche sicherstellen
    distance = radius_meters * math.sqrt(rng.random())
    angle = rng.uniform(0, 2 * math.pi)

    earth_radius = 6_378_137.0  # Meter
    lat_rad = math.radians(lat)
    delta_lat = (distance * math.cos(angle)) / earth_radius
    denom = earth_radius * math.cos(lat_rad)
    if abs(denom) < 1e-9:
        denom = earth_radius
    delta_lon = (distance * math.sin(angle)) / denom

    return lat + math.degrees(delta_lat), lon + math.degrees(delta_lon)

def _get_record_for_building_no_lock(building_id):
    if building_id in DB_BUILDINGS:
        return DB_BUILDINGS[building_id], 'registered'
    if building_id in DB_INTEREST_POOL:
        return DB_INTEREST_POOL[building_id], 'anonymous'
    return None, None

def _save_tokens_async():
    """Speichert Tokens asynchron im Hintergrund."""
    # Erstelle Kopien für Thread-Sicherheit
    verification_tokens = dict(DB_VERIFICATION_TOKENS)
    unsubscribe_tokens = dict(DB_UNSUBSCRIBE_TOKENS)
    created_at = dict(_token_created_at)
    token_history = dict(_token_history)
    
    token_persistence.save_tokens_async(verification_tokens, unsubscribe_tokens, created_at, token_history)

def invalidate_verification_tokens(building_id):
    tokens_to_remove = [
        token for token, info in DB_VERIFICATION_TOKENS.items()
        if info.get('building_id') == building_id
    ]
    for token in tokens_to_remove:
        DB_VERIFICATION_TOKENS.pop(token, None)
        _token_created_at.pop(f'verification_{token}', None)
    
    # Speichere asynchron
    if tokens_to_remove:
        _save_tokens_async()

def invalidate_unsubscribe_tokens(building_id):
    tokens_to_remove = [
        token for token, info in DB_UNSUBSCRIBE_TOKENS.items()
        if info.get('building_id') == building_id
    ]
    for token in tokens_to_remove:
        DB_UNSUBSCRIBE_TOKENS.pop(token, None)
        _token_created_at.pop(f'unsubscribe_{token}', None)
    
    # Speichere asynchron
    if tokens_to_remove:
        _save_tokens_async()

def _invalidate_tokens_for_building(building_id):
    invalidate_verification_tokens(building_id)
    invalidate_unsubscribe_tokens(building_id)

def issue_verification_token(building_id):
    # Invalidiere alte Tokens für diese building_id (verhindert, dass mehrere E-Mails den gleichen Token haben)
    invalidate_verification_tokens(building_id)
    
    # Erstelle neuen Token
    token = str(uuid.uuid4())
    DB_VERIFICATION_TOKENS[token] = {'building_id': building_id}
    _token_created_at[f'verification_{token}'] = time.time()
    
    # Speichere asynchron
    _save_tokens_async()
    
    return token

def issue_unsubscribe_token(building_id):
    # Prüfe, ob bereits ein Token für diese building_id existiert
    for token, info in DB_UNSUBSCRIBE_TOKENS.items():
        if info.get('building_id') == building_id:
            return token  # Wiederverwende bestehenden Token
    
    # Erstelle neuen Token, wenn keiner existiert
    token = str(uuid.uuid4())
    DB_UNSUBSCRIBE_TOKENS[token] = {'building_id': building_id}
    _token_created_at[f'unsubscribe_{token}'] = time.time()
    
    # Speichere asynchron
    _save_tokens_async()
    
    return token

def remove_building(building_id):
    removed = False
    with db_lock:
        if DB_BUILDINGS.pop(building_id, None):
            removed = True
        if DB_INTEREST_POOL.pop(building_id, None):
            removed = True
        if removed:
            _invalidate_tokens_for_building(building_id)
            CLUSTER_CONTACT_STATE.clear()
    if removed:
        threading.Thread(target=run_full_ml_task, daemon=True).start()
    return removed

def find_buildings_by_email(email):
    matches = []
    needle = email.lower()
    with db_lock:
        for store in (DB_BUILDINGS, DB_INTEREST_POOL):
            for building_id, data in store.items():
                value = (data.get('email') or '').lower()
                if value == needle:
                    matches.append(building_id)
    return matches

def send_verification_email(email, confirmation_url, unsubscribe_url):
    """Sendet Verifizierungs-E-Mail via SendGrid oder gibt sie in Logs aus"""
    subject = "BadenLEG – Bitte bestätigen Sie Ihre Teilnahme"
    message_body = (
        "Willkommen bei BadenLEG!\n\n"
        "Bitte bestätigen Sie, dass wir Ihre Kontaktdaten mit interessierten Nachbarinnen "
        "und Nachbarn teilen dürfen. Sobald Sie bestätigen, informieren wir alle passenden "
        "Haushalte und senden eine Übersicht mit Adresse und E-Mail aller bestätigten Teilnehmer:innen.\n\n"
        f"Klicken Sie hier zur Bestätigung:\n{confirmation_url}\n\n"
        "Mit dem Klick stimmen Sie dem Austausch Ihrer Kontaktdaten mit passenden Nachbarn zu.\n\n"
        f"Ich bin nicht mehr an einem LEG-Zusammenschluss interessiert und melde mich hiermit ab:\n"
        f"{unsubscribe_url}\n\n"
        "Ihr BadenLEG-Team"
    )

    if EMAIL_ENABLED:
        try:
            message = Mail(
                from_email=FROM_EMAIL,
                to_emails=email,
                subject=subject,
                plain_text_content=message_body
            )
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            logger.info(f"[EMAIL] Verifizierung gesendet an {email} (Status: {response.status_code})")
        except Exception as e:
            logger.error(f"[EMAIL] Fehler beim Senden an {email}: {e}")
            # Fallback: Log in Console
            print(f"\n--- [EMAIL VERIFIKATION - FEHLER] ---")
            print(f"AN: {email}")
            print(message_body)
            print("--------------------------------------\n")
    else:
        # Development Mode: Log to console
        print(f"\n--- [EMAIL VERIFIKATION] ---")
        print(f"AN: {email}")
        print(f"BETREFF: {subject}")
        print(message_body)
        print("-----------------------------\n")

def send_cluster_contact_email(cluster_id, cluster_info, verified_contacts):
    """Sendet Cluster-Kontaktübersicht via SendGrid oder gibt sie in Logs aus"""
    autarky = cluster_info.get('autarky_percent', 0.0)
    subject = f"BadenLEG – Ihre Nachbarn für die LEG-Gründung"
    
    header = (
        f"BadenLEG – Kontaktübersicht\n"
        f"Autarkie-Potenzial: {autarky:.1f}%\n\n"
        "Die folgenden Haushalte haben ihre Teilnahme bestätigt. Nutzen Sie die Kontaktdaten, "
        "um direkt miteinander in den Austausch zu gehen.\n\n"
    )

    table_header = "Adresse".ljust(45) + "E-Mail"
    table_separator = "-" * 45 + " " + "-" * 45
    rows = []
    for contact in verified_contacts:
        address = (contact.get('address') or "Adresse unbekannt").strip()
        email = contact.get('email') or "keine E-Mail angegeben"
        rows.append(address.ljust(45) + email)

    base_body = header + table_header + "\n" + table_separator + "\n" + "\n".join(rows) + "\n\n"
    base_body += "Viel Erfolg beim Aufbau Ihrer lokalen Elektrizitätsgemeinschaft!\nIhr BadenLEG-Team\n\n"

    for contact in verified_contacts:
        recipient = contact.get('email')
        if not recipient:
            continue
        unsubscribe_url = contact.get('unsubscribe_url')
        if not unsubscribe_url:
            token = issue_unsubscribe_token(contact['building_id'])
            unsubscribe_url = f"{APP_BASE_URL}/unsubscribe/{token}"
        
        body = (
            base_body +
            "Ich bin nicht mehr an einem LEG-Zusammenschluss interessiert und melde mich hiermit ab:\n"
            f"{unsubscribe_url}"
        )
        
        if EMAIL_ENABLED:
            try:
                message = Mail(
                    from_email=FROM_EMAIL,
                    to_emails=recipient,
                    subject=subject,
                    plain_text_content=body
                )
                sg = SendGridAPIClient(SENDGRID_API_KEY)
                response = sg.send(message)
                logger.info(f"[EMAIL] Kontaktübersicht gesendet an {recipient} (Status: {response.status_code})")
            except Exception as e:
                logger.error(f"[EMAIL] Fehler beim Senden an {recipient}: {e}")
                # Fallback: Log in Console
                print(f"\n--- [EMAIL KONTAKTÜBERSICHT - FEHLER] ---")
                print(f"AN: {recipient}")
                print(body)
                print("------------------------------------------\n")
        else:
            # Development Mode: Log to console
            print(f"\n--- [EMAIL KONTAKTÜBERSICHT] ---")
            print(f"AN: {recipient}")
            print(f"BETREFF: {subject}")
            print(body)
            print("---------------------------------\n")

def notify_existing_interested_persons(new_building_id, new_record):
    """
    Benachrichtigt alle bestehenden verifizierten Interessenten in derselben Zone,
    dass sich ein neuer Interessent eingetragen hat.
    Sendet die vollständige Kontaktübersicht (inklusive dem neuen Interessenten).
    """
    try:
        new_profile = new_record.get('profile', {})
        new_lat = new_profile.get('lat')
        new_lon = new_profile.get('lon')
        new_email = new_record.get('email')
        
        if not new_lat or not new_lon or not new_email:
            print(f"[NOTIFY] Keine vollständigen Daten für building_id {new_building_id}")
            return
        
        # Finde alle verifizierten Personen in einem 150m Radius
        verified_contacts = []
        with db_lock:
            # Durchsuche DB_BUILDINGS
            for building_id, record in DB_BUILDINGS.items():
                if building_id == new_building_id:
                    continue  # Überspringe die neue Person
                if not record.get('verified') or not record.get('email'):
                    continue
                profile = record.get('profile', {})
                p_lat = profile.get('lat')
                p_lon = profile.get('lon')
                if not p_lat or not p_lon:
                    continue
                
                # Berechne Distanz
                distance = ml_models.calculate_distance(new_lat, new_lon, p_lat, p_lon)
                if distance <= 150:  # 150m Radius
                    unsubscribe_token = issue_unsubscribe_token(building_id)
                    verified_contacts.append({
                        'building_id': building_id,
                        'email': record.get('email'),
                        'address': profile.get('address', ''),
                        'phone': record.get('phone', ''),
                        'unsubscribe_url': f"{APP_BASE_URL}/unsubscribe/{unsubscribe_token}"
                    })
            
            # Durchsuche DB_INTEREST_POOL
            for building_id, record in DB_INTEREST_POOL.items():
                if building_id == new_building_id:
                    continue  # Überspringe die neue Person
                if not record.get('verified') or not record.get('email'):
                    continue
                profile = record.get('profile', {})
                p_lat = profile.get('lat')
                p_lon = profile.get('lon')
                if not p_lat or not p_lon:
                    continue
                
                # Berechne Distanz
                distance = ml_models.calculate_distance(new_lat, new_lon, p_lat, p_lon)
                if distance <= 150:  # 150m Radius
                    unsubscribe_token = issue_unsubscribe_token(building_id)
                    verified_contacts.append({
                        'building_id': building_id,
                        'email': record.get('email'),
                        'address': profile.get('address', ''),
                        'phone': record.get('phone', ''),
                        'unsubscribe_url': f"{APP_BASE_URL}/unsubscribe/{unsubscribe_token}"
                    })
        
        # Wenn keine bestehenden Interessenten gefunden wurden, nichts tun
        if len(verified_contacts) == 0:
            print(f"[NOTIFY] Keine bestehenden verifizierten Interessenten in der Zone für building_id {new_building_id}")
            return
        
        # Füge den neuen Interessenten zur Liste hinzu
        new_unsubscribe_token = issue_unsubscribe_token(new_building_id)
        all_contacts = verified_contacts + [{
            'building_id': new_building_id,
            'email': new_email,
            'address': new_profile.get('address', ''),
            'phone': new_record.get('phone', ''),
            'unsubscribe_url': f"{APP_BASE_URL}/unsubscribe/{new_unsubscribe_token}",
            'is_new': True  # Markiere den neuen Interessenten
        }]
        
        # Sende E-Mail an alle bestehenden Interessenten
        subject = "BadenLEG – Neuer Interessent für LEG-Gründung in Ihrer Zone"
        
        header = (
            f"BadenLEG – Neuer Interessent\n\n"
            f"Ein neuer Interessent für eine Lokale Elektrizitätsgemeinschaft (LEG) hat sich in Ihrer Zone eingetragen.\n\n"
            f"Aktuelle Kontaktübersicht aller verifizierten Interessenten in Ihrer Zone:\n\n"
        )
        
        table_header = "Adresse".ljust(40) + "E-Mail".ljust(35) + "Mobile"
        table_separator = "-" * 40 + " " + "-" * 35 + " " + "-" * 20
        rows = []
        for contact in all_contacts:
            address = (contact.get('address') or "Adresse unbekannt").strip()
            email = contact.get('email') or "keine E-Mail angegeben"
            phone = contact.get('phone') or "-"
            marker = " (NEU)" if contact.get('is_new') else ""
            rows.append(f"{address}{marker}".ljust(42) + email.ljust(37) + phone)
        
        base_body = header + table_header + "\n" + table_separator + "\n" + "\n".join(rows) + "\n\n"
        base_body += "Nutzen Sie die Kontaktdaten, um direkt miteinander in den Austausch zu gehen.\n\n"
        base_body += "Viel Erfolg beim Aufbau Ihrer lokalen Elektrizitätsgemeinschaft!\nIhr BadenLEG-Team\n\n"
        
        # Sende E-Mail an alle bestehenden Interessenten (nicht an den neuen)
        for contact in verified_contacts:
            recipient = contact.get('email')
            if not recipient:
                continue
            
            unsubscribe_url = contact.get('unsubscribe_url')
            if not unsubscribe_url:
                token = issue_unsubscribe_token(contact['building_id'])
                unsubscribe_url = f"{APP_BASE_URL}/unsubscribe/{token}"
            
            body = (
                base_body +
                "Ich bin nicht mehr an einem LEG-Zusammenschluss interessiert und melde mich hiermit ab:\n"
                f"{unsubscribe_url}"
            )
            
            if EMAIL_ENABLED:
                try:
                    message = Mail(
                        from_email=FROM_EMAIL,
                        to_emails=recipient,
                        subject=subject,
                        plain_text_content=body
                    )
                    sg = SendGridAPIClient(SENDGRID_API_KEY)
                    response = sg.send(message)
                    logger.info(f"[EMAIL] Neuer Interessent-Benachrichtigung gesendet an {recipient} (Status: {response.status_code})")
                except Exception as e:
                    logger.error(f"[EMAIL] Fehler beim Senden an {recipient}: {e}")
                    print(f"\n--- [EMAIL NEUER INTERESSENT - FEHLER] ---")
                    print(f"AN: {recipient}")
                    print(body)
                    print("------------------------------------------\n")
            else:
                # Development Mode: Log to console
                print(f"\n--- [EMAIL NEUER INTERESSENT] ---")
                print(f"AN: {recipient}")
                print(f"BETREFF: {subject}")
                print(body)
                print("---------------------------------\n")
        
        print(f"[NOTIFY] {len(verified_contacts)} bestehende Interessenten über neuen Interessenten benachrichtigt")
        
    except Exception as e:
        print(f"[NOTIFY] Fehler beim Benachrichtigen bestehender Interessenten: {e}")
        import traceback
        traceback.print_exc()

def notify_cluster_contacts(buildings_with_clusters):
    pending_notifications = []
    with db_lock:
        for cluster_id, cluster_info in DB_CLUSTER_INFO.items():
            cluster_members = buildings_with_clusters[buildings_with_clusters['cluster'] == cluster_id]
            if cluster_members.empty:
                continue

            verified_contacts = []
            for _, row in cluster_members.iterrows():
                building_id = row.get('building_id')
                if not building_id:
                    continue
                record, _ = _get_record_for_building_no_lock(building_id)
                if not record or not record.get('email') or not record.get('verified'):
                    continue
                profile = record.get('profile', {})
                unsubscribe_token = issue_unsubscribe_token(building_id)
                verified_contacts.append({
                    'building_id': building_id,
                    'email': record.get('email'),
                    'address': profile.get('address'),
                    'unsubscribe_url': f"{APP_BASE_URL}/unsubscribe/{unsubscribe_token}"
                })

            if len(verified_contacts) < 2:
                continue

            member_set = frozenset(sorted(contact['building_id'] for contact in verified_contacts))
            if CLUSTER_CONTACT_STATE.get(cluster_id) == member_set:
                continue

            CLUSTER_CONTACT_STATE[cluster_id] = member_set
            pending_notifications.append((cluster_id, cluster_info, verified_contacts))

    for cluster_id, cluster_info, contacts in pending_notifications:
        send_cluster_contact_email(cluster_id, cluster_info, contacts)

def collect_building_locations(exclude_building_id=None):
    """
    Erstellt die Liste aller bekannten Standorte mit anonymisierter Position.
    Optional kann eine building_id ausgeschlossen werden (z.B. aktueller Nutzer).
    """
    locations = []
    with db_lock:
        for building_id, profile_data in DB_INTEREST_POOL.items():
            if exclude_building_id and building_id == exclude_building_id:
                continue
            profile = profile_data.get('profile', {})
            lat = profile.get('lat')
            lon = profile.get('lon')
            if lat is None or lon is None:
                continue
            jitter_lat, jitter_lon = jitter_coordinates(
                lat,
                lon,
                radius_meters=ANONYMITY_RADIUS_METERS,
                seed=profile.get('building_id') or building_id
            )
            locations.append({
                'lat': jitter_lat,
                'lon': jitter_lon,
                'type': 'anonymous'
            })
        for building_id, profile_data in DB_BUILDINGS.items():
            if exclude_building_id and building_id == exclude_building_id:
                continue
            profile = profile_data.get('profile', {})
            lat = profile.get('lat')
            lon = profile.get('lon')
            if lat is None or lon is None:
                continue
            jitter_lat, jitter_lon = jitter_coordinates(
                lat,
                lon,
                radius_meters=ANONYMITY_RADIUS_METERS,
                seed=profile.get('building_id') or building_id
            )
            locations.append({
                'lat': jitter_lat,
                'lon': jitter_lon,
                'type': 'registered'
            })
    return locations

def run_full_ml_task(new_building_id=None):
    """
    (ASYNC-TASK) Führt die langsame, vollständige ML-Analyse im Hintergrund aus.
    Aktualisiert den globalen Cache und stößt E-Mail-Matchings an.
    
    Args:
        new_building_id: Optional. Die building_id des neu registrierten Nutzers.
                        Wird aktuell nur geloggt.
    """
    print("\n[ASYNC-TASK] Starte langsame ML-Neuberechnung im Hintergrund...")
    
    all_profiles_list = get_all_known_profiles()
    
    if len(all_profiles_list) < 2:
        print("[ASYNC-TASK] -> Nicht genügend Nutzer für Clustering. Breche ab.")
        return

    # Erstelle DataFrame mit normalem Index, um Index-Probleme zu vermeiden
    building_data = pd.DataFrame(all_profiles_list)
    # Stelle sicher, dass wir einen normalen RangeIndex haben
    if not isinstance(building_data.index, pd.RangeIndex):
        building_data = pd.DataFrame(building_data.to_dict('records'))
    
    # 1. Langsame ML-Analyse ausführen
    ranked_communities, buildings_with_clusters = ml_models.find_optimal_communities(
        building_data,
        radius_meters=150,
        min_community_size=2
    )
    
    # 2. Globalen Cache aktualisieren
    with db_lock:
        DB_CLUSTERS.clear()
        DB_CLUSTER_INFO.clear()
        
        # Stelle sicher, dass building_id vorhanden ist
        if 'building_id' in buildings_with_clusters.columns:
            for _, row in buildings_with_clusters.iterrows():
                building_id = row.get('building_id')
                if building_id:
                    DB_CLUSTERS[building_id] = row.get('cluster', -1)
            
        for community in ranked_communities:
            DB_CLUSTER_INFO[community['community_id']] = community
            
    print(f"[ASYNC-TASK] -> ML-Cache aktualisiert: {len(ranked_communities)} Cluster gefunden.")

    # 3. Matches prüfen und Kontaktdaten versenden
    print("[ASYNC-TASK] -> Aktualisiere Kontaktübersichten für bestätigte Cluster...")
    notify_cluster_contacts(buildings_with_clusters)
    
    print("[ASYNC-TASK] -> Hintergrund-Task abgeschlossen.\n")


def find_provisional_matches(new_profile):
    """
    (FAST-TASK) Findet schnelle "provisorische" Matches für die sofortige API-Antwort.
    Prüft nur die Distanz, führt KEIN DBSCAN aus.
    """
    print("[API] Starte schnelle, provisorische Match-Suche...")
    all_profiles = get_all_known_profiles()
    
    if not all_profiles:
        return None # Der erste Nutzer findet nie ein Match
        
    new_coords = (new_profile['lat'], new_profile['lon'])
    provisional_cluster_profiles = [new_profile] # Starte Cluster mit neuem Nutzer
    
    for profile in all_profiles:
        existing_coords = (profile['lat'], profile['lon'])
        distance = ml_models.calculate_distance(new_coords[0], new_coords[1], existing_coords[0], existing_coords[1])
        
        if distance <= 150: # (Hardcodierter Radius)
            provisional_cluster_profiles.append(profile)
            
    if len(provisional_cluster_profiles) < 2:
        print("[API] -> Provisorische Suche: Keine Nachbarn gefunden.")
        return None # Kein Match
        
    print(f"[API] -> Provisorische Suche: {len(provisional_cluster_profiles)-1} Nachbarn gefunden.")
    
    # Erstelle einen temporären DataFrame, um Autarkie zu simulieren
    # Verwende to_dict('records') um sicherzustellen, dass wir einen normalen Index haben
    community_df = pd.DataFrame(provisional_cluster_profiles)
    # Stelle sicher, dass der Index normal ist
    if not isinstance(community_df.index, pd.RangeIndex):
        community_df = pd.DataFrame(community_df.to_dict('records'))
    
    # Simuliere Autarkie für DIESE KLEINE GRUPPE
    autarky_score, _, _ = ml_models.calculate_community_autarky(community_df, None)
    
    # Baue Cluster-Info für die API-Antwort (ohne ML-Clustering-ID)
    members = [{
        'building_id': p['building_id'], 
        'lat': p['lat'], 
        'lon': p['lon']} for p in provisional_cluster_profiles
    ]
    
    cluster_info = {
        'community_id': 'provisional', # Kennzeichnen als provisorisch
        'num_members': len(members),
        'members': members,
        'autarky_percent': autarky_score * 100,
    }
    
    return cluster_info

def build_match_result(profile):
    """
    Liefert provisorische Cluster-Informationen für die Sofort-Anzeige im Frontend.
    Kontaktdaten werden erst nach beidseitiger Bestätigung per E-Mail verschickt.
    """
    cluster_info = find_provisional_matches(profile)
    if not cluster_info:
        return None

    return {
        'community_id': cluster_info.get('community_id'),
        'num_members': cluster_info.get('num_members'),
        'autarky_percent': float(cluster_info.get('autarky_percent', 0.0)),
        'members': cluster_info.get('members', [])
    }


# --- API-Endpunkte ---

@app.route("/")
def index():
    """Zeigt das HTML-Frontend an."""
    if not os.path.exists('templates'):
        os.makedirs('templates')
        print("WARNUNG: 'templates'-Ordner wurde erstellt. Bitte 'index.html' dort ablegen.")
    return render_template('index.html', site_url=SITE_URL)


@app.route("/robots.txt")
def robots_txt():
    """Liefert robots.txt für Suchmaschinen."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "Disallow: /admin/",
        "Disallow: /confirm/",
        "Disallow: /unsubscribe/",
        f"Sitemap: {SITE_URL}/sitemap.xml"
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    """Einfaches Sitemap XML"""
    pages = [
        ("/", "1.0", "daily"),  # Homepage: Höchste Priorität
        ("/leg", "0.9", "weekly"),  # LEG-Seite: Sehr wichtig für SEO
        ("/evl", "0.8", "weekly"),  # EVL-Seite
        ("/zev", "0.8", "weekly"),  # ZEV-Seite
        ("/vergleich-leg-evl-zev", "0.8", "weekly"),  # Vergleichsseite
        ("/impressum", "0.3", "yearly"),  # Legal: Niedrige Priorität
        ("/datenschutz", "0.3", "yearly"),  # Legal: Niedrige Priorität
    ]
    xml = render_template("sitemap.xml", site_url=SITE_URL, pages=pages)
    return Response(xml, mimetype="application/xml")

@app.route("/health")
def health_check():
    """Einfache Health-Check-Route für Deployment-Überwachung"""
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "version": "1.0.0"
    })

@app.route("/api/suggest_addresses")
@limiter.limit("30 per minute") if limiter else lambda f: f
def api_suggest_addresses():
    """
    (Schnell) Gibt Adressvorschläge basierend auf einem Suchbegriff zurück.
    Je länger der Query, desto spezifischer die Ergebnisse.
    """
    query = request.args.get('q', '').strip()
    
    # Security: Sanitize query
    query = security_utils.sanitize_string(query, max_length=100)
    
    print(f"[API] Adressvorschläge angefragt für: '{query}'")
    
    if not query or len(query) < 2:
        return jsonify({"suggestions": []})
    
    # Mehr Ergebnisse für längere Queries (bessere Filterung)
    limit = 15 if len(query) < 5 else 10
    suggestions_raw = data_enricher.get_address_suggestions(query, limit=limit)
    print(f"[API] {len(suggestions_raw)} Vorschläge gefunden (nur Kanton Aargau)")
    
    # Filtere leere Labels und gib vollständige Objekte zurück
    suggestions = []
    for s in suggestions_raw:
        if isinstance(s, dict) and s.get('label') and s.get('label').strip():
            # Security: Sanitize label
            label = security_utils.sanitize_string(s.get('label', ''), max_length=200)
            if label:
                suggestions.append({
                    'label': label,
                    'lat': s.get('lat'),
                    'lon': s.get('lon'),
                    'plz': s.get('plz')
                })
    
    return jsonify({"suggestions": suggestions})

@app.route("/api/get_all_buildings")
def api_get_all_buildings():
    """
    (Schnell) Gibt die Koordinaten aller bekannten (anonymen
    und registrierten) Gebäude zurück, um die Karte zu füllen.
    """
    print("[API] Lade alle bekannten Gebäudepositionen...")
    locations = collect_building_locations()
    print(f"  -> {len(locations)} Gebäude gefunden.")
    return jsonify({"buildings": locations})

@app.route("/impressum")
def impressum():
    return render_template("impressum.html", site_url=SITE_URL)

@app.route("/datenschutz")
def datenschutz():
    return render_template("datenschutz.html", site_url=SITE_URL)

@app.route("/unsubscribe", methods=["GET", "POST"])
@limiter.limit("5 per minute") if limiter else lambda f: f
def unsubscribe_page():
    status = None
    message = None
    email_value = ""

    if request.method == "POST":
        email_value = (request.form.get("email") or "").strip()
        
        # Security: Validate email
        is_valid_email, normalized_email, email_error = security_utils.validate_email_address(email_value)
        if not is_valid_email:
            status = "error"
            message = email_error
            log_security_event("INVALID_EMAIL", f"unsubscribe: {email_error}", 'WARNING')
        else:
            email_value = normalized_email
            matches = find_buildings_by_email(email_value)
            if not matches:
                status = "info"
                message = "Für diese E-Mail-Adresse wurde kein Eintrag gefunden."
            else:
                removed_any = False
                for building_id in matches:
                    removed_any = remove_building(building_id) or removed_any
                if removed_any:
                    status = "success"
                    message = "Ihre Daten wurden erfolgreich abgemeldet."
                    log_security_event("UNSUBSCRIBED", f"Email: {email_value}", 'INFO')
                else:
                    status = "info"
                    message = "Für diese E-Mail-Adresse wurde kein aktiver Eintrag gefunden."

    return render_template(
        "unsubscribe.html",
        status=status,
        message=message,
        email=email_value,
        site_url=SITE_URL
    )

@app.route("/unsubscribe/<token>")
@limiter.limit("10 per minute") if limiter else lambda f: f
def unsubscribe_token(token):
    # Security: Validate token format
    is_valid_token, token_error = security_utils.validate_token(token)
    if not is_valid_token:
        log_security_event("INVALID_TOKEN", f"unsubscribe: {token_error}", 'WARNING')
        return "<h1>Ungültiger Link</h1><p>Der Abmeldelink hat ein ungültiges Format.</p>", 400
    
    with db_lock:
        token_info = DB_UNSUBSCRIBE_TOKENS.pop(token, None)
        if token_info:
            _token_created_at.pop(f'unsubscribe_{token}', None)
            _save_tokens_async()

    if not token_info:
        log_security_event("TOKEN_NOT_FOUND", f"unsubscribe: Token not found or already used", 'INFO')
        return "<h1>Link ungültig</h1><p>Dieser Abmeldelink ist ungültig oder wurde bereits verwendet.</p>", 404

    building_id = token_info['building_id']
    removed = remove_building(building_id)
    if removed:
        log_security_event("UNSUBSCRIBED_VIA_TOKEN", f"Building ID: {building_id}", 'INFO')
        return "<h1>Abmeldung erfolgreich</h1><p>Ihre Daten wurden gelöscht. Sie können sich jederzeit erneut registrieren.</p>"

    return "<h1>Kein Eintrag gefunden</h1><p>Für diesen Link konnte kein aktiver Eintrag ermittelt werden.</p>", 404

@app.route("/api/get_all_clusters")
def api_get_all_clusters():
    """
    (Schnell) Gibt alle existierenden ZEV/LEG-Cluster als Polygone zurück.
    """
    print("[API] Lade alle Cluster...")
    clusters = []
    
    try:
        with db_lock:
            for cluster_id, cluster_info in DB_CLUSTER_INFO.items():
                if cluster_id == 'provisional':
                    continue  # Überspringe provisorische Cluster
                
                # Erstelle Polygon-Koordinaten aus den Mitgliedern
                members = cluster_info.get('members', [])
                if len(members) < 2:
                    continue
                
                # Erstelle ein einfaches Polygon (Convex Hull) um die Punkte
                try:
                    coords = [[m.get('lat'), m.get('lon')] for m in members if m.get('lat') and m.get('lon')]
                    if len(coords) < 2:
                        continue
                    
                    # Einfacher Convex Hull Algorithmus
                    polygon_coords = create_simple_polygon(coords)
                    
                    clusters.append({
                        'cluster_id': cluster_id,
                        'members': members,
                        'polygon': polygon_coords,
                        'autarky_percent': cluster_info.get('autarky_percent', 0),
                        'num_members': cluster_info.get('num_members', len(members))
                    })
                except Exception as e:
                    print(f"  [API] Fehler beim Erstellen des Polygons für Cluster {cluster_id}: {e}")
                    continue
        
        print(f"  -> {len(clusters)} Cluster gefunden.")
        return jsonify({"clusters": clusters})
    except Exception as e:
        print(f"[API] Fehler in api_get_all_clusters: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"clusters": [], "error": str(e)}), 500

def create_simple_polygon(coords):
    """
    Erstellt ein einfaches Polygon um die gegebenen Koordinaten.
    Verwendet einen einfachen Convex Hull Ansatz.
    """
    if len(coords) < 3:
        # Wenn weniger als 3 Punkte, erstelle ein kleines Quadrat um den Punkt
        if len(coords) == 1:
            lat, lon = coords[0]
            offset = 0.0005  # ~50m
            return [
                [lat - offset, lon - offset],
                [lat + offset, lon - offset],
                [lat + offset, lon + offset],
                [lat - offset, lon + offset],
                [lat - offset, lon - offset]  # Schließe Polygon
            ]
        elif len(coords) == 2:
            # Erstelle ein Rechteck zwischen den beiden Punkten
            lat1, lon1 = coords[0]
            lat2, lon2 = coords[1]
            offset = 0.0003  # ~30m
            return [
                [lat1 - offset, lon1 - offset],
                [lat2 + offset, lon1 - offset],
                [lat2 + offset, lon2 + offset],
                [lat1 - offset, lon2 + offset],
                [lat1 - offset, lon1 - offset]
            ]
    
    # Für 3+ Punkte: Convex Hull oder einfaches Rechteck
    if HAS_SCIPY:
        try:
            points = np.array(coords)
            hull = ConvexHull(points)
            polygon = [coords[i] for i in hull.vertices]
            polygon.append(polygon[0])  # Schließe Polygon
            return polygon
        except:
            pass
    
    # Fallback: Einfaches Rechteck um alle Punkte
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    offset = 0.0003
    return [
        [min_lat - offset, min_lon - offset],
        [max_lat + offset, min_lon - offset],
        [max_lat + offset, max_lon + offset],
        [min_lat - offset, max_lon + offset],
        [min_lat - offset, min_lon - offset]
    ]


@app.route("/api/check_potential", methods=['POST'])
@limiter.limit("10 per minute") if limiter else lambda f: f
def api_check_potential():
    """
    (Schnell) Nimmt eine Adresse entgegen, reichert sie an und
    findet provisorische Matches.
    """
    try:
        # Security: Check request size
        is_valid_size, size_error = security_utils.check_request_size(request)
        if not is_valid_size:
            log_security_event("REQUEST_TOO_LARGE", f"check_potential: {size_error}", 'WARNING')
            return jsonify({"error": size_error}), 413
        
        if not request.json:
            log_security_event("INVALID_REQUEST", "check_potential: No JSON data", 'WARNING')
            return jsonify({"error": "Keine Daten empfangen."}), 400
            
        address = request.json.get('address', '').strip()
        
        # Security: Validate and sanitize address
        is_valid, sanitized_address, error_msg = security_utils.validate_address(address)
        if not is_valid:
            log_security_event("INVALID_INPUT", f"check_potential: {error_msg}", 'WARNING')
            return jsonify({"error": error_msg}), 400
        
        address = sanitized_address
            
        # 1. Adresse anreichern (verwende echte API, da wir die richtige URL haben)
        print(f"[API] Analysiere Adresse: '{address}'")
        estimates = None
        profiles = None
        
        try:
            estimates, profiles = data_enricher.get_energy_profile_for_address(address)
            if not estimates:
                # Fallback auf Mock, falls echte API fehlschlägt
                print(f"[API] Echte API fehlgeschlagen, verwende Mock für: '{address}'")
                estimates, profiles = data_enricher.get_mock_energy_profile_for_address(address)
        except Exception as e:
            print(f"[API] Fehler bei Adress-Analyse: {e}")
            import traceback
            traceback.print_exc()
            # Fallback auf Mock
            try:
                estimates, profiles = data_enricher.get_mock_energy_profile_for_address(address)
            except Exception as mock_error:
                print(f"[API] Auch Mock fehlgeschlagen: {mock_error}")
                return jsonify({"error": f"Adresse konnte nicht verarbeitet werden: {str(e)}"}), 500
        
        if not estimates:
            return jsonify({"error": "Adresse konnte nicht analysiert werden."}), 404
    except Exception as e:
        print(f"[API] Unerwarteter Fehler in api_check_potential: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Server-Fehler: {str(e)}"}), 500
        
    # 2. Schnelle, provisorische Match-Suche
    cluster_info = find_provisional_matches(estimates)
            
    if not cluster_info:
        # Szenario A: Kein Match
        return jsonify({
            "potential": False,
            "message": "Keine direkten Partner gefunden.",
            "profile_summary": estimates
        })
    else:
        # Szenario B: Match gefunden!
        return jsonify({
            "potential": True,
            "message": "Partner gefunden! Wir haben passende Nachbarn identifiziert.",
            "cluster_info": cluster_info, # Enthält jetzt Koordinaten
            "profile_summary": estimates
        })

@app.route("/api/register_anonymous", methods=['POST'])
@limiter.limit("5 per minute") if limiter else lambda f: f
def api_register_anonymous():
    """
    (Schnell) Speichert Nutzer im anonymen Pool und
    löst den langsamen ML-Task im Hintergrund aus.
    """
    # Security: Check request size
    is_valid_size, size_error = security_utils.check_request_size(request)
    if not is_valid_size:
        log_security_event("REQUEST_TOO_LARGE", f"register_anonymous: {size_error}", 'WARNING')
        return jsonify({"error": size_error}), 413
    
    phone = (request.json.get('phone') or '').strip()
    email = (request.json.get('email') or '').strip()
    profile = request.json.get('profile') 
    
    # Security: Validate email
    is_valid_email, normalized_email, email_error = security_utils.validate_email_address(email)
    if not is_valid_email:
        log_security_event("INVALID_EMAIL", f"register_anonymous: {email_error}", 'WARNING')
        return jsonify({"error": email_error}), 400
    
    email = normalized_email
    
    # Security: Validate phone (optional)
    if phone:
        is_valid_phone, normalized_phone, phone_error = security_utils.validate_phone(phone)
        if not is_valid_phone:
            log_security_event("INVALID_PHONE", f"register_anonymous: {phone_error}", 'WARNING')
            return jsonify({"error": phone_error}), 400
        phone = normalized_phone
    
    if not profile:
        log_security_event("INVALID_REQUEST", "register_anonymous: No profile data", 'WARNING')
        return jsonify({"error": "Profildaten fehlen."}), 400
        
    building_id = profile.get('building_id')
    
    # Security: Validate building_id
    is_valid_id, id_error = security_utils.validate_building_id(building_id)
    if not is_valid_id:
        log_security_event("INVALID_BUILDING_ID", f"register_anonymous: {id_error}", 'WARNING')
        return jsonify({"error": id_error}), 400
    
    # Security: Validate coordinates in profile
    lat = profile.get('lat')
    lon = profile.get('lon')
    is_valid_coords, coords_error = security_utils.validate_coordinates(lat, lon)
    if not is_valid_coords:
        log_security_event("INVALID_COORDINATES", f"register_anonymous: {coords_error}", 'WARNING')
        return jsonify({"error": coords_error}), 400
    with db_lock:
        # Tokens werden jetzt wiederverwendet (siehe issue_verification_token)
        entry = {
            "profile": profile,
            "registered_at": time.time(),
            "email": email,
            "verified": False,
            "verification_sent_at": time.time()
        }
        if phone:
            entry["phone"] = phone
        DB_INTEREST_POOL[building_id] = entry
        verification_token = issue_verification_token(building_id)
        unsubscribe_token = issue_unsubscribe_token(building_id)
    
    confirmation_url = f"{APP_BASE_URL}/confirm/{verification_token}"
    unsubscribe_url = f"{APP_BASE_URL}/unsubscribe/{unsubscribe_token}"
    send_verification_email(email, confirmation_url, unsubscribe_url)

    print(f"[DB] Anonymer Nutzer {building_id} gespeichert (Mail: {email}, Tel: {phone}).")
    print(f"  Aktuelle Pool-Grösse: {len(DB_INTEREST_POOL)}")
    
    # Hintergrund-Analyse anstoßen
    threading.Thread(target=run_full_ml_task, args=(building_id,)).start()
    
    cluster_info = build_match_result(profile)
    locations = collect_building_locations(exclude_building_id=building_id)
    response_payload = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True
    }
    if cluster_info:
        response_payload["cluster_info"] = cluster_info
    return jsonify(response_payload)

@app.route("/api/register_full", methods=['POST'])
@limiter.limit("5 per minute") if limiter else lambda f: f
def api_register_full():
    """
    (Schnell) Speichert Nutzer in der DB und
    löst den langsamen ML-Task im Hintergrund aus.
    """
    # Security: Check request size
    is_valid_size, size_error = security_utils.check_request_size(request)
    if not is_valid_size:
        log_security_event("REQUEST_TOO_LARGE", f"register_full: {size_error}", 'WARNING')
        return jsonify({"error": size_error}), 413
    
    profile = request.json.get('profile')
    email = (request.json.get('email') or '').strip()
    phone = (request.json.get('phone') or '').strip()
    
    # Security: Validate email
    is_valid_email, normalized_email, email_error = security_utils.validate_email_address(email)
    if not is_valid_email:
        log_security_event("INVALID_EMAIL", f"register_full: {email_error}", 'WARNING')
        return jsonify({"error": email_error}), 400
    
    email = normalized_email
    
    # Security: Validate phone (optional)
    if phone:
        is_valid_phone, normalized_phone, phone_error = security_utils.validate_phone(phone)
        if not is_valid_phone:
            log_security_event("INVALID_PHONE", f"register_full: {phone_error}", 'WARNING')
            return jsonify({"error": phone_error}), 400
        phone = normalized_phone
    
    if not profile:
        log_security_event("INVALID_REQUEST", "register_full: No profile data", 'WARNING')
        return jsonify({"error": "Profildaten fehlen."}), 400
        
    building_id = profile.get('building_id')
    
    # Security: Validate building_id
    is_valid_id, id_error = security_utils.validate_building_id(building_id)
    if not is_valid_id:
        log_security_event("INVALID_BUILDING_ID", f"register_full: {id_error}", 'WARNING')
        return jsonify({"error": id_error}), 400
    
    # Security: Validate coordinates in profile
    lat = profile.get('lat')
    lon = profile.get('lon')
    is_valid_coords, coords_error = security_utils.validate_coordinates(lat, lon)
    if not is_valid_coords:
        log_security_event("INVALID_COORDINATES", f"register_full: {coords_error}", 'WARNING')
        return jsonify({"error": coords_error}), 400
    with db_lock:
        # Tokens werden jetzt wiederverwendet (siehe issue_verification_token)
        entry = {
            "profile": profile,
            "registered_at": time.time(),
            "email": email,
            "verified": False,
            "verification_sent_at": time.time()
        }
        if phone:
            entry["phone"] = phone
        DB_BUILDINGS[building_id] = entry
        verification_token = issue_verification_token(building_id)
        unsubscribe_token = issue_unsubscribe_token(building_id)
    
    confirmation_url = f"{APP_BASE_URL}/confirm/{verification_token}"
    unsubscribe_url = f"{APP_BASE_URL}/unsubscribe/{unsubscribe_token}"
    send_verification_email(email, confirmation_url, unsubscribe_url)

    print(f"[DB] Voll registrierter Nutzer {building_id} gespeichert (Mail: {email}, Tel: {phone}).")
    print(f"  Aktuelle DB-Grösse: {len(DB_BUILDINGS)}")

    # Hintergrund-Analyse anstoßen
    threading.Thread(target=run_full_ml_task, args=(building_id,)).start()
    
    cluster_info = build_match_result(profile)
    locations = collect_building_locations(exclude_building_id=building_id)
    response_payload = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True
    }
    if cluster_info:
        response_payload["cluster_info"] = cluster_info
    return jsonify(response_payload)


@app.route("/confirm/<token>")
@limiter.limit("10 per minute") if limiter else lambda f: f
def confirm_match(token):
    """
    Wird von Nutzerinnen und Nutzern aufgerufen, um den Austausch der Kontaktdaten zu bestätigen.
    """
    # Security: Validate token format
    is_valid_token, token_error = security_utils.validate_token(token)
    if not is_valid_token:
        log_security_event("INVALID_TOKEN", f"confirm: {token_error}", 'WARNING')
        return "<h1>Ungültiger Link</h1><p>Der Bestätigungslink hat ein ungültiges Format.</p>", 400
    
    # OPTIMIERUNG: Prüfe zuerst, ob Token existiert und hole building_id
    with db_lock:
        token_info = DB_VERIFICATION_TOKENS.get(token)  # Nicht pop, nur get
    
    building_id = None
    if token_info:
        building_id = token_info.get('building_id')
        print(f"[CONFIRM] Token gefunden für building_id: {building_id}")
    else:
        print(f"[CONFIRM] Token nicht gefunden in DB_VERIFICATION_TOKENS (Anzahl: {len(DB_VERIFICATION_TOKENS)})")
        # Prüfe Token-Historie (für Tokens, die bereits verwendet wurden)
        history_key = f'verification_{token}'
        if history_key in _token_history:
            building_id = _token_history[history_key]
            print(f"[CONFIRM] Token in Historie gefunden für building_id: {building_id}")
    
    # MITIGATION: Wenn Token nicht existiert, prüfe ob Person bereits verifiziert ist
    # (z.B. nach App-Neustart, aber Person war bereits verifiziert)
    if not building_id:
        # Token existiert weder in aktiven Tokens noch in Historie
        log_security_event("TOKEN_NOT_FOUND", f"confirm: Token not found in tokens or history", 'INFO')
        return "<h1>Link ungültig</h1><p>Dieser Bestätigungslink ist ungültig oder wurde bereits verwendet. Falls Sie sich bereits registriert haben, sollten Sie bereits eine Kontaktübersicht erhalten haben.</p>", 404
    
    # Prüfe VOR dem Token-Pop, ob bereits verifiziert (verhindert "Link ungültig" bei bereits verifizierten)
    with db_lock:
        record, source = _get_record_for_building_no_lock(building_id)
        if not record:
            return "<h1>Profil nicht gefunden</h1><p>Der zugehörige Eintrag existiert nicht mehr.</p>", 404
        if record.get('verified'):
            # Token entfernen (Cleanup), aber zeige Erfolgsmeldung
            DB_VERIFICATION_TOKENS.pop(token, None)
            _token_created_at.pop(f'verification_{token}', None)
            # Speichere Token in Historie (falls noch nicht vorhanden)
            history_key = f'verification_{token}'
            if history_key not in _token_history:
                _token_history[history_key] = building_id
            _save_tokens_async()
            return "<h1>Bereits bestätigt</h1><p>Sie haben Ihre Teilnahme bereits bestätigt. Wir informieren Ihre Nachbarn bei Änderungen automatisch.</p>"
    
    # Token ist gültig und Person ist noch nicht verifiziert - entferne Token jetzt
    # ABER: Speichere Token in Historie, damit wir später prüfen können
    with db_lock:
        DB_VERIFICATION_TOKENS.pop(token, None)
        _token_created_at.pop(f'verification_{token}', None)
        # Speichere Token in Historie für spätere Prüfung
        _token_history[f'verification_{token}'] = building_id
        _save_tokens_async()
    
    log_security_event("EMAIL_CONFIRMED", f"Building ID: {building_id}", 'INFO')
    
    # Setze verified Flag
    with db_lock:
        record['verified'] = True
        record['verified_at'] = time.time()

        if source == 'registered':
            DB_BUILDINGS[building_id] = record
        elif source == 'anonymous':
            DB_INTEREST_POOL[building_id] = record

    # Benachrichtige alle bestehenden verifizierten Interessenten in derselben Zone
    threading.Thread(target=notify_existing_interested_persons, args=(building_id, record), daemon=True).start()
    
    # Nach erfolgreicher Verifizierung Cluster neu berechnen und Kontakte informieren
    threading.Thread(target=run_full_ml_task, daemon=True).start()
    
    return (
        "<h1>Vielen Dank!</h1>"
        "<p>Ihre Zustimmung wurde gespeichert. Wir senden Ihnen und allen passenden Nachbarinnen und Nachbarn "
        "in Kürze eine E-Mail mit Adresse und E-Mail aller bestätigten Haushalte.</p>"
    )


# ===============================
# Informationsseiten
# ===============================

@app.route("/leg")
def page_leg():
    """Erklärt Lokale Elektrizitätsgemeinschaft (LEG)"""
    return render_template("leg.html", site_url=SITE_URL)

@app.route("/evl")
def page_evl():
    """Erklärt Eigenverbrauchslösung (EVL) und virtuelle EVL (vEVL)"""
    return render_template("evl.html", site_url=SITE_URL)

@app.route("/zev")
def page_zev():
    """Erklärt Zusammenschluss zum Eigenverbrauch (ZEV) und vZEV"""
    return render_template("zev.html", site_url=SITE_URL)

@app.route("/vergleich-leg-evl-zev")
def page_vergleich():
    """Vergleicht LEG, EVL und ZEV"""
    return render_template("vergleich.html", site_url=SITE_URL)


if __name__ == "__main__":
    # Führe initiale ML-Analyse aus, falls bereits Gebäude vorhanden sind
    def initial_ml_analysis():
        time.sleep(2)  # Warte kurz, damit Server vollständig gestartet ist
        all_profiles = get_all_known_profiles()
        if len(all_profiles) >= 2:
            print("[INIT] Führe initiale ML-Analyse für vorhandene Gebäude durch...")
            run_full_ml_task()
    
    # Starte initiale Analyse im Hintergrund
    threading.Thread(target=initial_ml_analysis, daemon=True).start()
    
    app.run(debug=True, port=5003, host='127.0.0.1')


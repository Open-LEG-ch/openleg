"""
Token-Persistenz: Speichert Verifizierungs- und Unsubscribe-Tokens in einer JSON-Datei.
Verwendet atomic writes für Sicherheit und automatisches Aufräumen alter Tokens.
"""
import json
import os
import time
import threading
from pathlib import Path

# Token-Datei-Pfad
TOKEN_FILE = os.getenv('TOKEN_FILE', '/data/badenleg_tokens.json')
TOKEN_FILE_FALLBACK = './data/badenleg_tokens.json'

# TTL für Tokens (30 Tage in Sekunden)
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

# Lock für Thread-Sicherheit
_persistence_lock = threading.Lock()

def _get_token_file_path():
    """Gibt den Token-Datei-Pfad zurück, mit Fallback zu lokalem Verzeichnis."""
    if os.path.exists('/data') and os.access('/data', os.W_OK):
        return TOKEN_FILE
    else:
        # Fallback: Lokales Verzeichnis
        os.makedirs('./data', exist_ok=True)
        return TOKEN_FILE_FALLBACK

def load_tokens():
    """
    Lädt Tokens aus der JSON-Datei.
    Gibt (verification_tokens, unsubscribe_tokens, created_at) zurück.
    Räumt automatisch abgelaufene Tokens auf.
    """
    file_path = _get_token_file_path()
    
    if not os.path.exists(file_path):
        print(f"[TOKEN DB] Keine existierende Token-Datei gefunden bei {file_path}")
        print(f"[TOKEN DB] Starte mit leerer Token-Datenbank")
        return {}, {}, {}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        verification_tokens = data.get('verification_tokens', {})
        unsubscribe_tokens = data.get('unsubscribe_tokens', {})
        created_at = data.get('created_at', {})
        
        # Räume abgelaufene Tokens auf
        current_time = time.time()
        expired_verification = []
        expired_unsubscribe = []
        
        for token, info in verification_tokens.items():
            token_created = created_at.get(f'verification_{token}', current_time)
            if current_time - token_created > TOKEN_TTL_SECONDS:
                expired_verification.append(token)
        
        for token, info in unsubscribe_tokens.items():
            token_created = created_at.get(f'unsubscribe_{token}', current_time)
            if current_time - token_created > TOKEN_TTL_SECONDS:
                expired_unsubscribe.append(token)
        
        # Entferne abgelaufene Tokens
        for token in expired_verification:
            verification_tokens.pop(token, None)
            created_at.pop(f'verification_{token}', None)
        
        for token in expired_unsubscribe:
            unsubscribe_tokens.pop(token, None)
            created_at.pop(f'unsubscribe_{token}', None)
        
        if expired_verification or expired_unsubscribe:
            print(f"[TOKEN DB] {len(expired_verification)} abgelaufene Verification-Tokens entfernt")
            print(f"[TOKEN DB] {len(expired_unsubscribe)} abgelaufene Unsubscribe-Tokens entfernt")
            # Speichere aufgeräumte Tokens
            save_tokens(verification_tokens, unsubscribe_tokens, created_at)
        
        print(f"[TOKEN DB] {len(verification_tokens)} Verification-Tokens geladen")
        print(f"[TOKEN DB] {len(unsubscribe_tokens)} Unsubscribe-Tokens geladen")
        
        return verification_tokens, unsubscribe_tokens, created_at
        
    except json.JSONDecodeError as e:
        print(f"[TOKEN DB] Fehler beim Lesen der Token-Datei: {e}")
        print(f"[TOKEN DB] Starte mit leerer Token-Datenbank")
        return {}, {}, {}
    except Exception as e:
        print(f"[TOKEN DB] Unerwarteter Fehler beim Laden: {e}")
        print(f"[TOKEN DB] Starte mit leerer Token-Datenbank")
        return {}, {}, {}

def save_tokens(verification_tokens, unsubscribe_tokens, created_at=None):
    """
    Speichert Tokens in die JSON-Datei (ATOMIC WRITE).
    Verwendet temporäre Datei + rename für Sicherheit.
    """
    file_path = _get_token_file_path()
    temp_file = f"{file_path}.tmp"
    
    if created_at is None:
        # Lade bestehende created_at Daten, falls vorhanden
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                    created_at = existing_data.get('created_at', {})
            except:
                created_at = {}
    
    data = {
        'verification_tokens': verification_tokens,
        'unsubscribe_tokens': unsubscribe_tokens,
        'created_at': created_at,
        'last_updated': time.time()
    }
    
    try:
        # Atomic write: Schreibe zuerst in temporäre Datei
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Rename ist atomic auf den meisten Dateisystemen
        os.replace(temp_file, file_path)
        
        return True
    except Exception as e:
        print(f"[TOKEN DB] Fehler beim Speichern: {e}")
        # Versuche temporäre Datei zu löschen
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except:
            pass
        return False

def save_tokens_async(verification_tokens, unsubscribe_tokens, created_at=None):
    """
    Speichert Tokens asynchron im Hintergrund-Thread.
    Verhindert Blockierung der Haupt-Threads.
    """
    def _save():
        with _persistence_lock:
            save_tokens(verification_tokens, unsubscribe_tokens, created_at)
    
    threading.Thread(target=_save, daemon=True).start()

def update_token_created_at(token_type, token, created_at_dict):
    """
    Aktualisiert das Erstellungsdatum eines Tokens.
    Wird beim Generieren neuer Tokens aufgerufen.
    """
    key = f'{token_type}_{token}'
    created_at_dict[key] = time.time()


"""
Security utilities for OpenLEG application
Provides input validation, sanitization, and security helpers
"""

import re
import bleach
from email_validator import validate_email, EmailNotValidError
from urllib.parse import urlparse

# Allowed HTML tags for sanitization (none for our use case)
ALLOWED_TAGS = []
ALLOWED_ATTRIBUTES = {}

def sanitize_string(text, max_length=500):
    """
    Sanitize user input string to prevent XSS and injection attacks
    
    Args:
        text: Input string to sanitize
        max_length: Maximum allowed length
        
    Returns:
        Sanitized string
    """
    if not text:
        return ""
    
    # Remove any HTML tags
    text = bleach.clean(text, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)
    
    # Trim to max length
    text = text[:max_length]
    
    # Remove control characters except newlines and tabs
    text = ''.join(char for char in text if char.isprintable() or char in '\n\t')
    
    return text.strip()


def validate_email_address(email):
    """
    Validate and normalize email address
    
    Args:
        email: Email address string
        
    Returns:
        tuple: (is_valid, normalized_email, error_message)
    """
    if not email:
        return False, None, "E-Mail-Adresse ist erforderlich"
    
    # Basic length check
    if len(email) > 320:  # RFC 5321
        return False, None, "E-Mail-Adresse ist zu lang"
    
    try:
        # Validate and normalize
        valid = validate_email(email, check_deliverability=False)
        normalized_email = valid.normalized
        return True, normalized_email, None
    except EmailNotValidError as e:
        return False, None, f"Ungültige E-Mail-Adresse: {str(e)}"


def validate_address(address):
    """
    Validate address string
    
    Args:
        address: Address string
        
    Returns:
        tuple: (is_valid, sanitized_address, error_message)
    """
    if not address:
        return False, None, "Adresse ist erforderlich"
    
    # Sanitize
    sanitized = sanitize_string(address, max_length=200)
    
    if len(sanitized) < 5:
        return False, None, "Adresse ist zu kurz"
    
    # Check for basic address patterns (alphanumeric, spaces, commas, hyphens)
    if not re.match(r'^[a-zA-ZäöüÄÖÜßéèêàâ0-9\s,.\-]+$', sanitized):
        return False, None, "Adresse enthält ungültige Zeichen"
    
    return True, sanitized, None


def validate_phone(phone):
    """
    Validate phone number (Swiss format)
    
    Args:
        phone: Phone number string
        
    Returns:
        tuple: (is_valid, normalized_phone, error_message)
    """
    if not phone:
        return True, None, None  # Phone is optional
    
    # Remove all non-digit characters except +
    normalized = re.sub(r'[^\d+]', '', phone)
    
    # Swiss phone numbers: +41... or 0...
    if not re.match(r'^(\+41|0041|0)\d{9}$', normalized):
        return False, None, "Ungültige Schweizer Telefonnummer"
    
    # Normalize to +41 format
    if normalized.startswith('0041'):
        normalized = '+41' + normalized[4:]
    elif normalized.startswith('0'):
        normalized = '+41' + normalized[1:]
    
    return True, normalized, None


def validate_coordinates(lat, lon):
    """
    Validate latitude and longitude
    
    Args:
        lat: Latitude
        lon: Longitude
        
    Returns:
        tuple: (is_valid, error_message)
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (ValueError, TypeError):
        return False, "Ungültige Koordinaten"
    
    # Check reasonable bounds for Switzerland
    # Switzerland roughly: 45.8-47.8°N, 5.9-10.5°E
    if not (45.0 <= lat <= 48.0):
        return False, "Breitengrad ausserhalb des gültigen Bereichs"
    
    if not (5.0 <= lon <= 11.0):
        return False, "Längengrad ausserhalb des gültigen Bereichs"
    
    return True, None


def validate_building_id(building_id):
    """
    Validate building ID format
    
    Args:
        building_id: Building ID string
        
    Returns:
        tuple: (is_valid, error_message)
    """
    if not building_id:
        return False, "Gebäude-ID ist erforderlich"
    
    # Should be alphanumeric with underscores/hyphens
    if not re.match(r'^[a-zA-Z0-9_-]+$', str(building_id)):
        return False, "Ungültige Gebäude-ID"
    
    if len(str(building_id)) > 100:
        return False, "Gebäude-ID ist zu lang"
    
    return True, None


def validate_token(token):
    """
    Validate UUID token format
    
    Args:
        token: Token string
        
    Returns:
        tuple: (is_valid, error_message)
    """
    if not token:
        return False, "Token ist erforderlich"
    
    # UUID format: 8-4-4-4-12 hex characters
    if not re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', str(token).lower()):
        return False, "Ungültiges Token-Format"
    
    return True, None


def is_safe_redirect_url(url, allowed_hosts=None):
    """
    Check if redirect URL is safe (prevents open redirect vulnerabilities)
    
    Args:
        url: URL to check
        allowed_hosts: List of allowed hostnames
        
    Returns:
        bool: True if URL is safe
    """
    if not url:
        return True
    
    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    
    # Relative URLs are safe
    if not parsed.netloc:
        return True
    
    # Check against allowed hosts
    if allowed_hosts:
        return parsed.netloc in allowed_hosts
    
    return False


def sanitize_json_output(data):
    """
    Sanitize data before JSON output
    Prevents potential XSS in JSON responses
    
    Args:
        data: Dictionary or list to sanitize
        
    Returns:
        Sanitized data
    """
    if isinstance(data, dict):
        return {k: sanitize_json_output(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_json_output(item) for item in data]
    elif isinstance(data, str):
        # Escape HTML special characters in strings
        return (data
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#x27;')
                .replace('/', '&#x2F;'))
    else:
        return data


def check_request_size(request, max_content_length=1024 * 1024):
    """
    Check if request size is within limits
    
    Args:
        request: Flask request object
        max_content_length: Maximum allowed content length in bytes
        
    Returns:
        tuple: (is_valid, error_message)
    """
    content_length = request.content_length
    
    if content_length is None:
        return True, None
    
    if content_length > max_content_length:
        return False, "Anfrage ist zu gross"
    
    return True, None


def rate_limit_key_func():
    """
    Generate rate limit key based on IP address
    For use with Flask-Limiter
    """
    from flask import request
    # Use X-Forwarded-For if behind proxy, otherwise use remote_addr
    return request.headers.get('X-Forwarded-For', request.remote_addr)


# Security headers configuration
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'X-XSS-Protection': '1; mode=block',
    'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
}

# Content Security Policy
CSP_POLICY = {
    'default-src': ["'self'"],
    'script-src': [
        "'self'",
        "https://cdn.tailwindcss.com",
        "https://unpkg.com",
        "'unsafe-inline'"  # Required for Tailwind and Leaflet
    ],
    'style-src': [
        "'self'",
        "https://unpkg.com",
        "'unsafe-inline'"  # Required for Tailwind
    ],
    'img-src': [
        "'self'",
        "data:",
        "https:",
        "http:"  # For map tiles
    ],
    'font-src': [
        "'self'",
        "data:"
    ],
    'connect-src': [
        "'self'"
    ],
    'frame-ancestors': ["'none'"],
    'base-uri': ["'self'"],
    'form-action': ["'self'"],
}


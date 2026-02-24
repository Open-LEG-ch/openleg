# Security Documentation - OpenLEG

## Overview

OpenLEG implements multiple layers of security to protect user data and prevent common web vulnerabilities. This document outlines the security measures implemented and deployment recommendations.

## Implemented Security Features

### 1. Input Validation & Sanitization

All user inputs are validated and sanitized to prevent injection attacks:

- **Email validation**: RFC-compliant email validation with normalization
- **Address validation**: Alphanumeric and special character whitelisting
- **Phone validation**: Swiss phone number format validation
- **Coordinate validation**: Geographic bounds checking for Switzerland
- **Token validation**: UUID format validation
- **HTML sanitization**: All user-generated content is stripped of HTML tags

**Implementation**: `security_utils.py`

### 2. Rate Limiting

Protects against DoS attacks and brute force attempts:

- **Global limits**: 200 requests/hour, 50 requests/minute per IP
- **API endpoints**:
  - Address suggestions: 30/minute
  - Address checking: 10/minute
  - Registration: 5/minute
  - Confirmation/Unsubscribe: 10/minute

**Implementation**: Flask-Limiter with in-memory storage (Redis recommended for production)

### 3. Security Headers

Comprehensive security headers via Flask-Talisman:

- **HSTS**: HTTP Strict Transport Security (1 year max-age)
- **CSP**: Content Security Policy preventing XSS
- **X-Frame-Options**: DENY (clickjacking protection)
- **X-Content-Type-Options**: nosniff
- **Referrer-Policy**: strict-origin-when-cross-origin
- **Permissions-Policy**: Restricts browser features

### 4. HTTPS/TLS

- **Development**: HTTP allowed for localhost
- **Production**: Automatic HTTPS redirect enforced
- **Configuration**: `FLASK_ENV=production` in `.env`

### 5. Session Security

- **Secure cookies**: HTTPOnly, Secure (in production), SameSite=Lax
- **Secret key**: Cryptographically secure random key
- **Session lifetime**: Configurable (default: 1 hour)

### 6. Request Size Limits

- **Max content length**: 1MB per request
- Prevents memory exhaustion attacks

### 7. Data Anonymization

- **Coordinate jittering**: 120m radius for map display
- **Deterministic randomization**: Consistent location per building
- No exact addresses stored or displayed

### 8. Security Logging

All security events are logged to `openleg_security.log`:

- Invalid input attempts
- Token validation failures
- Rate limit violations
- Registration/unsubscribe events
- IP addresses for forensics

### 9. Token-Based Actions

- **UUIDs**: Cryptographically secure unique tokens
- **Single-use**: Tokens invalidated after use
- **Expiration**: Old tokens automatically cleaned up
- **Unguessable**: 128-bit entropy

### 10. Email Security

- **Validation**: Prevents email injection
- **Normalization**: Consistent format
- **No display**: Email addresses only shared after mutual confirmation
- **Unsubscribe**: One-click unsubscribe in all emails

## Environment Variables

### Required for Production

```bash
# CRITICAL: Change these before deployment
SECRET_KEY=generate-a-strong-random-key-here

# Application
FLASK_ENV=production
FLASK_DEBUG=False
APP_BASE_URL=https://openleg.ch

# Security
SESSION_COOKIE_SECURE=True
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE=Lax

# Rate Limiting (Redis recommended)
RATELIMIT_STORAGE_URL=redis://localhost:6379

# SMTP (if using real email)
SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USER=noreply@openleg.ch
SMTP_PASSWORD=your-secure-password
```

### Generating a Secure Secret Key

```python
import secrets
print(secrets.token_hex(32))
```

## Deployment Checklist

### Before Deployment

- [ ] Set `SECRET_KEY` to a strong random value
- [ ] Set `FLASK_ENV=production`
- [ ] Set `FLASK_DEBUG=False`
- [ ] Configure HTTPS/TLS certificate
- [ ] Update `APP_BASE_URL` to production domain
- [ ] Set up Redis for rate limiting (optional but recommended)
- [ ] Configure SMTP for real email sending
- [ ] Review and restrict `ALLOWED_HOSTS`
- [ ] Enable `SESSION_COOKIE_SECURE=True`
- [ ] Set up log rotation for `openleg_security.log`
- [ ] Configure firewall rules
- [ ] Set up automated backups (if using persistent storage)

### Infrastructure Recommendations

#### Web Server

Use a production WSGI server instead of Flask's development server:

```bash
# Install Gunicorn
pip install gunicorn

# Run with Gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

#### Reverse Proxy

Use Nginx or Apache as a reverse proxy:

```nginx
server {
    listen 443 ssl http2;
    server_name openleg.ch;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Security headers (additional layer)
    add_header X-Frame-Options "DENY";
    add_header X-Content-Type-Options "nosniff";
    add_header X-XSS-Protection "1; mode=block";
}
```

#### Redis for Rate Limiting

```bash
# Install Redis
sudo apt-get install redis-server

# Start Redis
sudo systemctl start redis
sudo systemctl enable redis

# Update .env
RATELIMIT_STORAGE_URL=redis://localhost:6379
```

#### Firewall

```bash
# Allow only necessary ports
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP (redirect to HTTPS)
sudo ufw allow 443/tcp  # HTTPS
sudo ufw enable
```

## Monitoring

### Security Logs

Monitor `openleg_security.log` for:

- Repeated invalid input attempts (potential attacks)
- Rate limit violations (DoS attempts)
- Invalid token access (brute force attempts)
- Unusual registration patterns

### Log Rotation

```bash
# /etc/logrotate.d/openleg
/path/to/openleg_security.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
}
```

## Known Limitations

### Current Implementation

1. **In-Memory Database**: Data is lost on server restart. For production, implement persistent storage (PostgreSQL recommended).

2. **No CSRF Protection for API**: Since this is a stateless API without traditional forms, CSRF is less of a concern. However, for future forms, implement Flask-WTF.

3. **Email Simulation**: Currently using print statements. Implement real SMTP before deployment.

4. **No User Authentication**: System is email-based without passwords. This is by design for simplicity, but consider adding authentication for admin functions.

## Future Security Enhancements

### Recommended for Production

1. **Database Encryption**: Encrypt sensitive data at rest
2. **API Keys**: For authenticated API access
3. **2FA**: Two-factor authentication for sensitive actions
4. **Rate Limiting by Email**: Prevent single user from spamming
5. **CAPTCHA**: On registration forms to prevent bots
6. **IP Reputation Checking**: Block known malicious IPs
7. **Audit Trail**: Comprehensive logging of all data changes
8. **Automated Security Scanning**: Regular vulnerability scans
9. **WAF**: Web Application Firewall (Cloudflare, AWS WAF)
10. **DDoS Protection**: CloudFlare or similar service

## Vulnerability Reporting

If you discover a security vulnerability, please email:

**hallo@openleg.ch**

Please do NOT publicly disclose security issues without coordination.

## Compliance

### GDPR Compliance

- ✅ Data minimization: Only essential data collected
- ✅ Right to be forgotten: Unsubscribe functionality
- ✅ Data portability: Email-based export possible
- ✅ Privacy policy: Available at /datenschutz
- ✅ Consent: Explicit email confirmation required
- ✅ Data anonymization: Coordinates jittered

### Swiss Data Protection Act (DSG)

- ✅ Transparency: Clear privacy policy
- ✅ Data security: Multiple security layers
- ✅ No third-party sharing: Explicitly stated
- ✅ User rights: Access, deletion, correction

## Security Contacts

- **Technical Lead**: Sihl Icon Valley
- **Email**: hallo@openleg.ch
- **Emergency**: Use email above

## Version History

- **v1.0.0** (2024-11): Initial security implementation
  - Input validation
  - Rate limiting
  - Security headers
  - Logging
  - Data anonymization

---

Last updated: November 2024


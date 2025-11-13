# BadenLEG - Lokale Elektrizitätsgemeinschaft Platform

BadenLEG is a web platform that helps residents of Baden, Switzerland find neighbors to form Local Electricity Communities (LEG - Lokale Elektrizitätsgemeinschaft) starting January 1, 2026.

## Features

- **Address-based matching**: Find potential LEG partners in your neighborhood
- **Interactive map**: Visualize existing interest clusters and potential communities
- **Privacy-first**: Coordinates are anonymized with 120m radius jittering
- **Email verification**: Two-step confirmation process for contact exchange
- **ML-powered clustering**: DBSCAN algorithm for optimal community formation
- **Educational content**: Comprehensive explanations of LEG, EVL/vEVL, and ZEV/vZEV
- **Security hardened**: Multiple layers of security (rate limiting, input validation, secure headers)
- **GDPR compliant**: Right to be forgotten, data minimization, explicit consent

## Technology Stack

- **Backend**: Flask (Python 3.11+)
- **Frontend**: HTML, TailwindCSS, Leaflet.js
- **ML**: scikit-learn (DBSCAN clustering)
- **Security**: Flask-Limiter, Flask-Talisman, bleach, email-validator
- **Data**: pandas, numpy, scipy

## Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/yourorg/badenleg.git
cd badenleg

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy environment template
cp env.example .env

# Edit .env and set at minimum:
# SECRET_KEY=<generate-a-secure-key>
# APP_BASE_URL=http://localhost:5003
```

### 3. Run Development Server

```bash
python app.py
```

Visit: http://localhost:5003

## Development Workflow

### Branch Structure

- **`main`**: Production branch - automatically deploys to badenleg.ch
- **`develop`**: Development branch - integration branch for features
- **`feature/*`**: Feature branches - individual feature development

### Workflow

1. **Start a new feature**:
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/your-feature-name
   ```

2. **Develop and commit**:
   ```bash
   # Make your changes
   git add .
   git commit -m "Add feature description"
   git push origin feature/your-feature-name
   ```

3. **Create Pull Request**:
   - Go to GitHub: https://github.com/wgusta/badenleg
   - Create PR: `feature/your-feature-name` → `develop`
   - Request review and merge after approval

4. **Deploy to Production**:
   ```bash
   git checkout main
   git pull origin main
   git merge develop
   git push origin main
   ```
   - GitHub Actions automatically deploys to Infomaniak
   - Site updates at https://badenleg.ch

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment instructions.

## Project Structure

```
badenleg/
├── app.py                      # Main Flask application
├── security_utils.py           # Security validation and sanitization
├── data_enricher.py            # Address geocoding and energy profile estimation
├── ml_models.py                # DBSCAN clustering for community formation
├── requirements.txt            # Python dependencies
├── env.example                 # Environment variables template
├── .gitignore                  # Git ignore patterns
├── SECURITY.md                 # Security documentation
├── DEPLOYMENT.md               # Deployment guide
├── README.md                   # This file
└── templates/
    ├── index.html              # Main UI
    ├── leg.html                # LEG explanation page
    ├── evl.html                # EVL/vEVL explanation page
    ├── zev.html                # ZEV/vZEV explanation page
    ├── vergleich.html          # Comparison page
    ├── impressum.html          # Imprint/legal
    ├── datenschutz.html        # Privacy policy
    └── unsubscribe.html        # Unsubscribe form
```

## Security Features

### Implemented

✅ **Input Validation**: All inputs validated and sanitized  
✅ **Rate Limiting**: Protection against DoS and brute force  
✅ **Security Headers**: CSP, HSTS, X-Frame-Options, etc.  
✅ **HTTPS Enforcement**: Automatic redirect in production  
✅ **Session Security**: Secure, HTTPOnly, SameSite cookies  
✅ **Request Size Limits**: 1MB max to prevent memory exhaustion  
✅ **Data Anonymization**: 120m coordinate jittering  
✅ **Security Logging**: All events logged for forensics  
✅ **Token-Based Actions**: UUIDs for email confirmation/unsubscribe  
✅ **Email Validation**: RFC-compliant with normalization  

See [SECURITY.md](SECURITY.md) for detailed security documentation.

## API Endpoints

### Public Endpoints

- `GET /` - Main application UI
- `GET /leg` - LEG explanation page
- `GET /evl` - EVL/vEVL explanation page
- `GET /zev` - ZEV/vZEV explanation page
- `GET /vergleich-leg-evl-zev` - Model comparison
- `GET /impressum` - Imprint
- `GET /datenschutz` - Privacy policy
- `GET /unsubscribe` - Unsubscribe form
- `GET /unsubscribe/<token>` - Direct unsubscribe via email link
- `GET /confirm/<token>` - Email confirmation

### API Endpoints

- `GET /api/suggest_addresses?q=<query>` - Address autocomplete
- `GET /api/get_all_buildings` - Get all registered building locations (anonymized)
- `GET /api/get_all_clusters` - Get all cluster polygons
- `POST /api/check_potential` - Check if address has potential matches
- `POST /api/register_anonymous` - Register interest (email only)
- `POST /api/register_full` - Full registration

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for comprehensive deployment guide.

### Quick Production Deployment

```bash
# Install security dependencies
pip install -r requirements.txt

# Configure environment
cp env.example .env
# Edit .env with production values

# Run with Gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 --timeout 120 app:app
```

### Docker Deployment

```bash
docker-compose up -d
```

## Development

### Running Tests

```bash
# Install dev dependencies
pip install pytest pytest-flask

# Run tests
pytest
```

### Code Style

```bash
# Install formatters
pip install black flake8

# Format code
black .

# Lint
flake8 app.py security_utils.py
```

## Configuration

### Environment Variables

See `env.example` for all available configuration options.

**Critical for Production:**

```bash
SECRET_KEY=<generate-with-secrets.token_hex(32)>
FLASK_ENV=production
FLASK_DEBUG=False
APP_BASE_URL=https://badenleg.ch
SESSION_COOKIE_SECURE=True
```

## Data Privacy

BadenLEG is designed with privacy as a core principle:

- **Data minimization**: Only email and approximate location collected
- **Anonymization**: Coordinates jittered by 120m before display
- **Explicit consent**: Two-step email confirmation required
- **Right to be forgotten**: One-click unsubscribe
- **No third-party sharing**: Data never shared outside the platform
- **Transparent**: Clear privacy policy at /datenschutz

## GDPR Compliance

✅ Lawful basis: Explicit consent  
✅ Data minimization: Only essential data  
✅ Purpose limitation: Only for LEG matching  
✅ Storage limitation: Unsubscribe removes all data  
✅ Integrity and confidentiality: Multiple security layers  
✅ Accountability: Logging and audit trail  
✅ Rights of data subjects: Access, rectification, erasure, portability  

## FAQ

### What is a LEG?

A Local Electricity Community (Lokale Elektrizitätsgemeinschaft) is a new model starting January 1, 2026 that allows neighbors to share locally produced solar electricity within a community or municipality.

See [/leg](http://localhost:5003/leg) for detailed explanation.

### How does the matching work?

1. Enter your address
2. System analyzes energy profile and finds potential matches using DBSCAN clustering
3. If matches found, register with email
4. Confirm via email (2-step verification)
5. Once confirmed, receive contact details of all confirmed neighbors in your cluster

### Is my address publicly visible?

No. Addresses are:
- Never displayed publicly
- Coordinates are jittered by 120m radius on the map
- Only shared after mutual email confirmation
- Can be removed anytime via unsubscribe

### What's the difference between LEG, EVL, and ZEV?

- **LEG**: Entire neighborhood/municipality (available 2026)
- **EVL/vEVL**: Single building or direct neighbors (available now, simple billing)
- **ZEV/vZEV**: Single building or area (available now, self-managed billing)

See [comparison page](http://localhost:5003/vergleich-leg-evl-zev) for details.

## Support

- **Email**: leg@sihliconvalley.ch
- **Website**: badenleg.ch
- **Issues**: [GitHub Issues](https://github.com/yourorg/badenleg/issues)

## License

[To be determined]

## Credits

**Developed by**: Sihl Icon Valley @ sihliconvalley.ch

**Powered by**:
- City of Baden energy data
- OpenStreetMap for geocoding
- Regionalwerke Baden for LEG framework

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## Changelog

### v1.0.0 (2024-11)

- Initial release
- Address-based matching
- Interactive map with clusters
- Email-based verification (2-step)
- Educational content pages (LEG/EVL/ZEV)
- Comprehensive security implementation
- GDPR-compliant data handling
- Unsubscribe functionality
- Security logging
- Rate limiting
- Input validation

## Roadmap

### v1.1 (Planned)

- [ ] Real SMTP integration
- [ ] Database persistence (PostgreSQL)
- [ ] Admin dashboard
- [ ] Email templates (HTML)
- [ ] Multi-language support (DE/FR/IT)

### v2.0 (Future)

- [ ] User accounts with login
- [ ] Chat/messaging between confirmed neighbors
- [ ] Document sharing
- [ ] Event organization
- [ ] Integration with Regionalwerke Baden API

## Acknowledgments

Thank you to:
- City of Baden for supporting local energy communities
- Regionalwerke Baden for LEG framework and technical support
- All early adopters helping test the platform

---

**BadenLEG** - Gemeinsam nachhaltig

Last updated: November 2024

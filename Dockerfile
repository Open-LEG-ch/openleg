FROM python:3.11-slim

# Apply security fixes published since the Python base image was built.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    gfortran \
    libopenblas-dev \
    curl \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libseccomp2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# CVE-2026-23949 (jaraco.context), CVE-2026-24049 (wheel): upgrade the
# base-image package that vendors both dependencies.
RUN pip install --no-cache-dir --upgrade "setuptools>=84.0.0"

COPY *.py ./
COPY store/ store/
COPY templates/ templates/
COPY translations/ translations/
COPY static/ static/
COPY scripts/ scripts/
COPY babel.cfg .
RUN python scripts/translations.py compile

RUN test -f /app/app.py \
    && test -f /app/templates/dashboard.html \
    && test -f /app/static/css/openleg.css

# Run the server unprivileged (trivy DS-0002). The application source stays
# owned by root. The service user owns /data for fresh volumes and /app/data,
# where the documented default SWISSELDEX_SDAT_DIR=data/sdat stores scheduled
# SDAT downloads. The home directory gives gunicorn's control server somewhere
# to write.
RUN groupadd --system --gid 10001 openleg \
    && useradd --system --uid 10001 --gid openleg --create-home --home-dir /var/lib/openleg \
       --shell /usr/sbin/nologin openleg \
    && mkdir -p /data /app/data/sdat \
    && chown openleg:openleg /data /app/data /app/data/sdat

USER openleg

EXPOSE 5000

CMD ["gunicorn", "wsgi:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--preload", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--timeout", "120"]

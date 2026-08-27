FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    gfortran \
    libopenblas-dev \
    curl \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
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
COPY static/ static/
COPY scripts/ scripts/

RUN test -f /app/app.py \
    && test -f /app/templates/index.html \
    && test -f /app/static/css/openleg.css

RUN mkdir -p /data

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

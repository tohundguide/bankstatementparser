FROM python:3.11-slim

# Install system dependencies (Tesseract OCR + Poppler for PDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies first (Docker cache optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p uploads output feedback learned/cache learned/profiles

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV PORT=8080
ENV SCRIPT_NAME=/tools/bankstatementparser

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/tools/bankstatementparser/status')" || exit 1

# Run with Gunicorn (production WSGI server)
# 1 worker keeps memory bounded; threads handle light concurrency.
# Timeout = 300s — large statements (long text PDFs / OCR) can take minutes to
# extract; must be >= Caddy's read/write timeouts so the proxy doesn't cut first.
CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT} \
    --workers 1 \
    --threads 2 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile -

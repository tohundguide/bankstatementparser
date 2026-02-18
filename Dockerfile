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
ENV PORT=5000

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/status')" || exit 1

# Run with Gunicorn (production WSGI server)
# Workers = 2 (good for 1-2 CPU cores on free tiers)
# Timeout = 120s (bank parsing can take time for large files)
CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT} \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -

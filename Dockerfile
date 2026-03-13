FROM python:3.12-slim

# Install system dependencies first (Cached)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tini && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

# Install Python requirements (Cached separately)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code (Last layer)
COPY . .
RUN chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD uvicorn not_my_nana_web:app --host 0.0.0.0 --port ${PORT:-8000}

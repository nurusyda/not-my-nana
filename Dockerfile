# 1. Use a stable Python version
FROM python:3.12-slim

# 2. Install Tesseract OCR and its dependencies
RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    --no-install-recommends \
    tesseract-ocr \
    libtesseract-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. Create a non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# 4. Set the working directory
WORKDIR /app

# 5. Copy requirements first 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy the rest of the project
# 6. Copy the rest of the project
COPY . .

# 7. Give the appuser permission to the files
RUN chown -R appuser:appuser /app

# 8. Switch to the secure user
USER appuser

# 9. Start the server using the PORT provided by Railway
CMD ["sh", "-c", "exec uvicorn not_my_nana_web:app --host 0.0.0.0 --port ${PORT:-8000}"]
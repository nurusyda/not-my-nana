# 1. Use a stable Python version
FROM python:3.12-slim

# 2. Install Tesseract OCR and its dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. Set the working directory
WORKDIR /app

# 4. Copy requirements first (for faster builds)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the project
COPY . .

# 6. Start the server using the PORT provided by Railway
CMD uvicorn not_my_nana_web:app --host 0.0.0.0 --port $PORT
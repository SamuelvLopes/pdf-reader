FROM python:3.11-slim

# Install system dependencies
# qpdf: for removing passwords
# poppler-utils: for pdftotext
RUN apt-get update && apt-get install -y \
    qpdf \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Expose port
EXPOSE 8787

# Run application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787"]

# PDF Tools Service

Self-hosted API for PDF password removal and text extraction. Designed for integration with n8n.

## Features
- Removes passwords from PDFs (using `qpdf`)
- Extracts text from PDFs (using `pdftotext`)
- Returns structured JSON response
- Dockerized and ready for production
- CI/CD pipeline for GitHub Container Registry

## Prerequisites
- Docker
- Docker Compose

## Quick Start (Local)

1. **Build and Run**
   ```bash
   docker-compose up --build -d
   ```

2. **Test with curl**

   *Without password:*
   ```bash
   curl -X POST -F "file=@/path/to/your/file.pdf" http://localhost:8787/extract
   ```

   *With password:*
   ```bash
   curl -X POST \
     -F "file=@/path/to/protected.pdf" \
     -F "password=your_password" \
     http://localhost:8787/extract
   ```

## API Endpoint

### `POST /extract`

**Parameters:**
- `file` (Multimedia): The PDF file to process.
- `password` (String, Optional): Password for the PDF if encrypted.


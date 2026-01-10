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

**Responses:**

*Success (200 OK):*
```json
{
  "success": true,
  "pages": 5,
  "text": "Extracted text content..."
}
```

*Error (400 Bad Request):*
```json
{
  "error": "invalid pdf password"
}
```

## n8n Integration

To use this service in n8n:

1. Add an **HTTP Request** node.
2. Set **Method** to `POST`.
3. Set **URL** to `http://your-docker-service:8787/extract`.
   - If running n8n in Docker locally, use `http://host.docker.internal:8787/extract` (ensure extra_hosts are configured) or put them in the same network.
4. Enable **Send Binary Data** (if passing file from previous node) or **Body Parameters**.
   - **Body Content Type**: `Form-Data`
   - **Parameter**: `file` (type: File)
   - **Parameter**: `password` (type: String, optional)

## CI/CD

The project includes a GitHub Action (`.github/workflows/docker.yml`) that automatically builds and pushes the Docker image to GitHub Container Registry (ghcr.io) on push to `main`.

**Image Tags:**
- `latest`
- `sha-<commit_hash>`
# pdf-reader

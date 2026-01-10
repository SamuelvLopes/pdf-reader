import os
import shutil
import subprocess
import tempfile
import uuid
import json
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="PDF Tools API")

def run_command(command):
    """Run a shell command and return the output."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        # qpdf returns exit code 2 on persistent errors (like bad password)
        # pdftotext might also fail
        raise Exception(f"Command failed: {e.stderr}")

@app.post("/extract")
async def extract_text(
    file: UploadFile = File(...),
    password: Optional[str] = Form(None)
):
    # Create a temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input_{uuid.uuid4()}.pdf")
        decrypted_path = os.path.join(temp_dir, f"decrypted_{uuid.uuid4()}.pdf")
        
        # Save uploaded file
        try:
            with open(input_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
             raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

        # Process PDF
        target_pdf = input_path
        
        # If password is provided or file is encrypted, try to decrypt with qpdf
        # We always run qpdf to ensure we have a clean, decrypted version if possible.
        # qpdf --password=PASSWORD --decrypt input.pdf output.pdf
        
        qpdf_cmd = ["qpdf", "--decrypt", input_path, decrypted_path]
        if password:
            qpdf_cmd.insert(1, f"--password={password}")
        else:
             # Try with empty password just in case, but qpdf handles non-encrypted fine
             qpdf_cmd.insert(1, "--password=")

        try:
            subprocess.run(
                qpdf_cmd,
                capture_output=True,
                check=True,
                text=True
            )
            target_pdf = decrypted_path
        except subprocess.CalledProcessError as e:
            # Check for specific password error from qpdf
            error_msg = e.stderr.lower()
            if "invalid password" in error_msg or "password" in error_msg:
                 return JSONResponse(
                    status_code=400,
                    content={"error": "invalid pdf password"}
                )
            # If it failed for other reasons, we might still try pdftotext on original if it wasn't encryption issue,
            # but usually for this task we want to fail if decryption fails.
            # However, if no password was provided and it failed, maybe it needs one.
            if "password not found" in error_msg or "password incorrect" in error_msg:
                 return JSONResponse(
                    status_code=400,
                    content={"error": "invalid pdf password"}
                )
            
            # Fallback for unexpected errors
            raise HTTPException(status_code=500, detail=f"PDF processing failed: {e.stderr}")

        # Extract text using pdftotext
        # pdftotext -layout input.pdf -
        try:
            # getting page count first (optional but requested in response)
            # pdfinfo input.pdf
            pdfinfo_cmd = ["pdfinfo", target_pdf]
            info_output = run_command(pdfinfo_cmd)
            pages = 0
            for line in info_output.splitlines():
                if "Pages:" in line:
                    pages = int(line.split(":")[1].strip())
                    break
            
            # extract text
            text_cmd = ["pdftotext", "-layout", target_pdf, "-"]
            text_content = run_command(text_cmd)
            
            return {
                "success": True,
                "pages": pages,
                "text": text_content
            }
            
        except Exception as e:
             raise HTTPException(status_code=500, detail=f"Text extraction failed: {str(e)}")

@app.get("/health")
def health_check():
    return {"status": "ok"}

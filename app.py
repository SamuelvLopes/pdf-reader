import os
import shutil
import subprocess
import tempfile
import uuid
import json
import re
from typing import Optional
from datetime import datetime

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
            
            # Extract structured data
            structured_data = parse_neoenergia_pe(text_content)
            
            return {
                "success": True,
                "pages": pages,
                "structured": structured_data,
                "text": text_content
            }
            
        except Exception as e:
             raise HTTPException(status_code=500, detail=f"Text extraction failed: {str(e)}")

def only_digits(s: str | None) -> str | None:
    if not s:
        return None
    d = re.sub(r"\D+", "", s)
    return d or None

def br_money_to_float(s: str | None) -> float | None:
    if not s:
        return None
    # "415,90" -> "415.90" | "1.234,56" -> "1234.56"
    x = s.strip()
    x = x.replace(".", "").replace(",", ".")
    try:
        return float(x)
    except ValueError:
        return None

def br_date_to_iso(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    # aceita dd/mm/yyyy ou dd/mm/yy
    m = re.match(r"^(\d{2})/(\d{2})/(\d{2,4})$", s)
    if not m:
        return None
    dd, mm, yy = m.group(1), m.group(2), m.group(3)
    if len(yy) == 2:
        yy = "20" + yy  # ajuste simples
    try:
        return datetime(int(yy), int(mm), int(dd)).date().isoformat()
    except ValueError:
        return None

def find_linha_digitavel(text: str) -> str | None:
    patterns = [
        # Boleto bancário (47 dígitos com pontos e espaços)
        r"\b(\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})\b",
        # Arrecadação (48 dígitos em 4 blocos)
        r"\b(\d{11,12}\s+\d{11,12}\s+\d{11,12}\s+\d{11,12})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            digits = only_digits(m.group(1))
            if digits and len(digits) in (44, 47, 48):
                return digits

    # Fallback por linha: sequência numérica longa com espaços/pontos
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if re.fullmatch(r"[\d\.\s]+", candidate):
            digits = only_digits(candidate)
            if digits and len(digits) in (44, 47, 48):
                return digits
    return None

def parse_neoenergia_pe(text: str) -> dict:
    out = {
        "supplier": {"name": None, "cnpj": None, "state_registration": None},
        "customer": {"name": None, "cpf_masked": None},
        "installation_code": None,
        "customer_code": None,
        "reference_month_year": None,
        "invoice": {"number": None, "series": None, "issue_date": None},
        "due_date": None,
        "total_amount": None,
        "currency": "BRL",
        "access_key": None,
        "authorization_protocol": {"number": None, "datetime": None},
        "barcode": {"linha_digitavel": None},
        "meter_readings": [],
    }

    # supplier name
    m = re.search(r"\n\s*(COMPANHIA ENERGÉTICA DE PERNAMBUCO)\s*\n", text, re.IGNORECASE)
    if m:
        out["supplier"]["name"] = m.group(1).strip().upper()

    # supplier CNPJ + IE
    m = re.search(r"CNPJ\s+([\d\.\-\/]+)\s+INSCRIÇÃO ESTADUAL\s+([0-9\.\-]+)", text, re.IGNORECASE)
    if m:
        out["supplier"]["cnpj"] = only_digits(m.group(1))
        out["supplier"]["state_registration"] = m.group(2).strip()

    # customer name
    m = re.search(r"NOME DO CLIENTE:\s*\n([^\n]+)", text, re.IGNORECASE)
    if m:
        line = m.group(1).strip()
        # Clean specific pollution from layout (e.g. "CÓDIGO DA INSTALAÇÃO" on same line)
        line = re.split(r"\s{2,}CÓDIGO DA INSTALAÇÃO", line, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        out["customer"]["name"] = line

    # cpf masked
    m = re.search(r"CPF:\s*([0-9\.\*\-]+)", text, re.IGNORECASE)
    if m:
        out["customer"]["cpf_masked"] = m.group(1).strip()

    # installation code
    # Heuristic: look for 6-10 digits after ENDEREÇO and before NOTA FISCAL
    m = re.search(r"ENDEREÇO:\s*\n(?:.*\n){0,3}\s*(\d{6,10})\s+NOTA FISCAL", text, re.IGNORECASE)
    if m:
        out["installation_code"] = m.group(1)

    # customer code
    # 1. Try footer first (stable)
    m = re.search(r"CÓDIGO DO CLIENTE\s+(\d{10})\b", text, re.IGNORECASE)
    if m:
        out["customer_code"] = m.group(1)
    
    # 2. If not found, look for 10 digits near the label (window)
    if not out["customer_code"]:
        m = re.search(r"CÓDIGO DO CLIENTE\s*\n([\s\S]{0,300})", text, re.IGNORECASE)
        if m:
            chunk = m.group(1)
            m2 = re.search(r"\b(\d{10})\b", chunk)
            if m2:
                out["customer_code"] = m2.group(1)

    # invoice number, series, issue date
    m = re.search(r"NOTA FISCAL N[°º]\s*(\d+)\s*-\s*SÉRIE\s*(\d+)\s*/\s*DATA DE EMISSÃO:\s*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
    if m:
        out["invoice"]["number"] = m.group(1)
        out["invoice"]["series"] = m.group(2)
        out["invoice"]["issue_date"] = br_date_to_iso(m.group(3))

    # reference month/year + total + due date
    m = re.search(
        r"REF:MÊS/ANO\s+TOTAL A PAGAR R\$\s+VENCIMENTO\s*\n\s*([0-9]{2}/[0-9]{4})\s+([0-9\.,]+)\s+([0-9]{2}/[0-9]{2}/[0-9]{4})",
        text, re.IGNORECASE
    )
    if m:
        out["reference_month_year"] = m.group(1).strip()
        out["total_amount"] = br_money_to_float(m.group(2))
        out["due_date"] = br_date_to_iso(m.group(3))

    # access key (strict: look for 11 groups of 4 digits with word boundaries)
    # This prevents capturing suffixes of previous numbers (like "0065" from customer code)
    # Pattern: 4 digits, followed by 10 groups of (space + 4 digits)
    m = re.search(r"\b\d{4}(?:\s+\d{4}){10}\b", text)
    if m:
        out["access_key"] = only_digits(m.group(0))
    else:
        # Fallback: Just look for a stricter block of digits that might be the key
        # We search specifically for the label "chave de acesso" and try to find the pattern nearby
        # WITHOUT merging everything first.
        m = re.search(r"chave de acesso:[\s\S]{0,100}?\b(\d{4}(?:\s+\d{4}){10})\b", text, re.IGNORECASE)
        if m:
            out["access_key"] = only_digits(m.group(1))

    # protocol + datetime
    m = re.search(r"Protocolo de autorização:\s*(\d+)\s*-\s*(\d{2}/\d{2}/\d{4})\s*às\s*(\d{2}:\d{2}:\d{2})", text, re.IGNORECASE)
    if m:
        out["authorization_protocol"]["number"] = m.group(1)
        iso_date = br_date_to_iso(m.group(2))
        if iso_date:
            out["authorization_protocol"]["datetime"] = f"{iso_date}T{m.group(3)}"

    # linha digitável (boleto/arrecadação)
    out["barcode"]["linha_digitavel"] = find_linha_digitavel(text)

    # Items Parsing
    items = []
    lines = text.splitlines()
    parsing_items = False
    
    for line in lines:
        if "ITENS DA FATURA" in line:
            parsing_items = True
            continue
            
        if parsing_items:
            # Stop conditions
            # Usually ends before breakdown table or consumption stats
            if "CONSUMO / kWh" in line or "TOTAL" in line.strip().upper() or line.strip().startswith("MEDIDOR"):
                if items: parsing_items = False
                continue # Don't break immediately to allow skipping empty lines, but usually safe to stop
            
            line_str = line.strip()
            if not line_str: continue
            
            # Skip headers
            if "UNID." in line or "PREÇO UNIT." in line or "COM TRIB." in line or "ICMS" in line and "TARIFA" in line:
                continue

            # Parsing Item Line
            # Multa/Juros de NF: descrição + referência da nota + valor monetário no fim
            multa_juros_match = re.match(
                r"^(?P<desc>(?:Multa|Juros)-NF)\s+(?P<ref>\d{5,})\s+(?P<amount>\d{1,3}(?:\.\d{3})*,\d{2})$",
                line_str,
                re.IGNORECASE
            )
            if multa_juros_match:
                items.append({
                    "description": multa_juros_match.group("desc"),
                    "reference_invoice": multa_juros_match.group("ref"),
                    "unit": None,
                    "quantity": None,
                    "unit_price": None,
                    "amount": br_money_to_float(multa_juros_match.group("amount"))
                })
                continue

            # Parsing Item Line
            # Regex for Complex line: Desc + Unit + Quant + Price + Amount
            # "Consumo-TUSD kWh 317,67 0,66209102 210,32"
            match_complex = re.match(r"^(.+?)\s+(kWh)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)", line_str)
            if match_complex:
                items.append({
                    "description": match_complex.group(1).strip(),
                    "unit": match_complex.group(2),
                    "quantity": br_money_to_float(match_complex.group(3)),
                    "unit_price": br_money_to_float(match_complex.group(4)),
                    "amount": br_money_to_float(match_complex.group(5))
                })
                continue
            
            # Parsing Simple Line: descrição + valor monetário final da linha
            # "Ilum. Púb. Municipal 35,91"
            match_simple = re.search(r"^(?P<desc>.+?)\s+(?P<amount>\d{1,3}(?:\.\d{3})*,\d{2})\s*$", line_str)
            if match_simple:
                desc = match_simple.group("desc").strip()
                val = br_money_to_float(match_simple.group("amount"))
                
                # Filter out obvious non-items or tax table rows
                if desc in ["PIS", "COFINS", "ICMS"] and val > 100: pass
                elif "ICMS" in desc and "TARIFA" in desc: pass
                else: 
                     items.append({
                        "description": desc,
                        "unit": None,
                        "quantity": None,
                        "unit_price": None,
                        "amount": val
                     })
    
    out["items"] = items

    # Meter readings table (MEDIDOR/LEITURAS/CONSUMO)
    meter_readings = []
    meter_block = []
    in_meter_block = False
    for line in lines:
        if not in_meter_block and "MEDIDOR" in line and "CONSUMO" in line:
            in_meter_block = True
            continue
        if in_meter_block:
            stripped = line.strip()
            if not stripped:
                # allow multiple blank lines before ending
                if meter_block:
                    break
                continue
            if "RESERVADO AO FISCO" in stripped or "ATENÇÃO" in stripped:
                break
            meter_block.append(stripped)

    meter_pattern = re.compile(
        r"^(?P<meter>\d+)\s+"
        r"(?P<magnitude>[A-Za-zÀ-ÿ\s]+?)\s+"
        r"(?P<slot>Único|Ponta|Fora\s+Ponta|Intermediário)\s+"
        r"(?P<prev>\d{1,3}(?:\.\d{3})*,\d{2})\s+"
        r"(?P<curr>\d{1,3}(?:\.\d{3})*,\d{2})\s+"
        r"(?P<const>\d+,\d+)\s+"
        r"(?P<kwh>\d{1,3}(?:\.\d{3})*,\d{2})"
    )

    for line in meter_block:
        m = meter_pattern.search(line)
        if m:
            meter_readings.append({
                "meter_number": m.group("meter"),
                "magnitude": m.group("magnitude").strip(),
                "time_slot": re.sub(r"\s+", " ", m.group("slot")).strip(),
                "previous_reading": br_money_to_float(m.group("prev")),
                "current_reading": br_money_to_float(m.group("curr")),
                "meter_constant": br_money_to_float(m.group("const")),
                "consumption_kwh": br_money_to_float(m.group("kwh")),
            })

    # Fallback para layout alternativo:
    # MEDIDOR <id>
    # LEITURA ANTERIOR dd/mm/yyyy
    # LEITURA ATUAL dd/mm/yyyy
    if not meter_readings:
        fallback_meter = re.search(
            r"MEDIDOR\s+([A-Z0-9]+)[\s\S]{0,120}?LEITURA\s+ANTERIOR\s+(\d{2}/\d{2}/\d{2,4})[\s\S]{0,120}?LEITURA\s+ATUAL\s+(\d{2}/\d{2}/\d{2,4})",
            text,
            re.IGNORECASE
        )
        if fallback_meter:
            meter_readings.append({
                "meter": fallback_meter.group(1),
                "previous_reading_date": br_date_to_iso(fallback_meter.group(2)),
                "current_reading_date": br_date_to_iso(fallback_meter.group(3)),
            })

    out["meter_readings"] = meter_readings

    # Validation
    warnings = []
    if not out["customer_code"]:
        warnings.append("customer_code_not_found")
    
    if not out["access_key"]:
        warnings.append("access_key_not_found")
    elif len(out["access_key"]) != 44:
        warnings.append("invalid_access_key_length")
        
    if not items:
        warnings.append("items_not_found")

    if not meter_readings:
        warnings.append("meter_readings_not_found")

    items_total = None
    if items:
        items_total = round(sum(item.get("amount") or 0.0 for item in items), 2)
    if items_total is not None and out["total_amount"] is not None:
        diff = abs(items_total - out["total_amount"])
        if diff > 0.01:
            warnings.append(f"items_total_mismatch:{diff:.2f}")
    
    out["validation"] = {
        "ok": len(warnings) == 0,
        "warnings": warnings,
        "items_total": items_total
    }

    return out


@app.get("/health")
def health_check():
    return {"status": "ok"}

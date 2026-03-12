import os
import re
import io
import json
import base64
import time
import binascii
import requests
from PIL import Image, ImageDraw
import pytesseract
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from cachetools import TTLCache

from prompts import STEP1_ANALYSIS_PROMPT, STEP2_EMPATHY_PROMPT

# --- WINDOWS TESSERACT CONFIG ---
if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

load_dotenv()
NOVA_API_KEY = os.getenv("NOVA_API_KEY")

if not NOVA_API_KEY:
    print("❌ ERROR: .env file found, but NOVA_API_KEY is empty!")
    raise RuntimeError("NOVA_API_KEY environment variable is missing")
else:
    print("✅ NOVA_API_KEY loaded")

app = FastAPI(title="Not My Nana ❤️")
templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- THE SECURITY & RATE LIMITING CONSTANTS ---
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
RATE_LIMIT_REQUESTS = 15
RATE_LIMIT_WINDOW_SECONDS = 60
request_history = TTLCache(maxsize=1024, ttl=RATE_LIMIT_WINDOW_SECONDS)

# --- THE PII SCRUBBER & OCR ---
def scrub_image_and_extract_text(img_bytes):
    try:
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        draw = ImageDraw.Draw(image)
        
        # 1. OCR Stage
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        
        # 2. THE BULLETPROOF REGEX
        pii_pattern = re.compile(
            r'('
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|'           
            r'(\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4,}|' 
            r'\b\d{4}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b|'            
            r'\b\d{13,19}\b'                                             
            r')',
            re.IGNORECASE
        )

        # 3. Redaction Stage (Logic Fix: No double loops)
        full_text = " ".join(data['text'])
        scrubbed_words = list(data['text']) 

        for match in pii_pattern.finditer(full_text):
            start_char = match.start()
            end_char = match.end()

            current_char_pos = 0
            for i in range(len(data['text'])):
                word = data['text'][i]
                word_len = len(word)
                
                word_start = current_char_pos
                word_end = current_char_pos + word_len
                
                if word_start < end_char and word_end > start_char:
                    if word.strip(): 
                        x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                        draw.rectangle([x, y, x + w, y + h], fill="black")
                        scrubbed_words[i] = "[REDACTED]"
                
                current_char_pos += word_len + 1 

        # 4. Save base64
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG")
        redacted_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        return redacted_b64, " ".join(scrubbed_words)
        
    except Exception as e:
        print(f"⚠️ Redaction Error: {e}")
        raise ValueError("PII redaction failed") from e

# --- PWA MANIFEST ---
@app.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name": "Not My Nana",
        "short_name": "Not My Nana",
        "description": "Protecting our loved ones from digital scams.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#c45c5c",
        "icons": [
            {"src": "/static/logo-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/logo-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    })

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- THE ANALYZE ENDPOINT ---
@app.post("/analyze")
async def analyze(payload: dict, request: Request):
    b64 = payload.get("base64")
    if not b64:
        raise HTTPException(status_code=400, detail="Missing image data")

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    history = request_history.get(client_ip, [])
    history = [t for t in history if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(history) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many requests")
    history.append(now)
    request_history[client_ip] = history

    try:
        # A. BASE64 DECODE
        try:
            img_bytes = base64.b64decode(b64)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid base64 image data")

        if len(img_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="Image too large (max 5MB)")

        allowed_mimes = {"jpeg", "jpg", "png", "gif", "webp"}
        mime = payload.get("mime", "jpeg").lower()
        if mime not in allowed_mimes:
            raise HTTPException(status_code=415, detail="Unsupported image format")

        # B. SCRUB AND READ TEXT
        safe_b64, extracted_text = scrub_image_and_extract_text(img_bytes)
        ocr_context = extracted_text if extracted_text.strip() else "[No text detected by local OCR. Use visual clues.]"

        headers = {"Authorization": f"Bearer {NOVA_API_KEY}", "Content-Type": "application/json"}
        
        # C. CALL 1: THE DETECTIVE
        resp1 = requests.post(
            "https://api.nova.amazon.com/v1/chat/completions",
            json={
                "model": "nova-2-lite-v1",
                "messages": [
                    {"role": "system", "content": STEP1_ANALYSIS_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": f"OCR Text: {ocr_context}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{safe_b64}"}}
                    ]}
                ],
                "temperature": 0.0
            },
            headers=headers, timeout=25
        )
        resp1.raise_for_status()
        raw_det = resp1.json()["choices"][0]["message"]["content"]
        
        try:
            start_det = raw_det.find('{')
            end_det = raw_det.rfind('}') + 1
            analysis_data = json.loads(raw_det[start_det:end_det])
        except Exception:
            print(f"🕵️ Detective failed to give JSON. Raw: {raw_det}")
            raise ValueError("Detective response was not valid JSON")

        print(f"🕵️ DETECTIVE: {analysis_data}")

        # D. CALL 2: THE GRANDCHILD
        resp2 = requests.post(
            "https://api.nova.amazon.com/v1/chat/completions",
            json={
                "model": "nova-2-lite-v1",
                "messages": [
                    {"role": "system", "content": STEP2_EMPATHY_PROMPT},
                    {"role": "user", "content": f"Findings: {json.dumps(analysis_data)}"}
                ],
                "temperature": 0.0
            },
            headers=headers, timeout=30
        )
        resp2.raise_for_status()
        raw_emp = resp2.json()["choices"][0]["message"]["content"]
        
        try:
            start_emp = raw_emp.find('{')
            end_emp = raw_emp.rfind('}') + 1
            empathy_data = json.loads(raw_emp[start_emp:end_emp])
        except Exception:
            print(f"❤️ Grandchild failed to give JSON. Raw: {raw_emp}")
            raise ValueError("Grandchild response was not valid JSON")

        print(f"❤️ GRANDCHILD: {empathy_data}")

        return {
            "category": analysis_data.get("category"),
            "is_ai": analysis_data.get("is_ai", False),
            "scam_probability": analysis_data.get("scam_probability", 0),
            "title": empathy_data.get("title", "Check Results"),
            "grandma_reply": empathy_data.get("grandma_reply", "Something went wrong. ❤️")
        }

    except HTTPException:
        # Pass through FastAPI exceptions (like rate limits)
        raise
    except Exception as e:
        import traceback
        print(f"🚨 PIPELINE ERROR: {e}")
        traceback.print_exc() 
        return {
            "category": "caution",
            "scam_probability": 50,
            "title": "🔌 Connection Error",
            "grandma_reply": "❤️ Nana, my brain is having trouble connecting. Please try again in a moment ❤️"
        }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("🚀 Not My Nana — Protected Multi-Step Mode")
    uvicorn.run(app, host="0.0.0.0", port=port)
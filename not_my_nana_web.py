import os
import re
import io
import json
import base64
import time
import asyncio
import httpx  # Faster, non-blocking replacement for requests
import binascii
from PIL import Image, ImageDraw, UnidentifiedImageError
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
    print("❌ ERROR: NOVA_API_KEY is missing!")
    raise RuntimeError("NOVA_API_KEY environment variable is missing")

app = FastAPI(title="Not My Nana ❤️")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- THE SECURITY & RATE LIMITING CONSTANTS ---
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000  # Shield against "Decompression Bombs"
RATE_LIMIT_REQUESTS = 15
RATE_LIMIT_WINDOW_SECONDS = 60
request_history = TTLCache(maxsize=1024, ttl=RATE_LIMIT_WINDOW_SECONDS)

# --- THE PII SCRUBBER & OCR ---
def scrub_image_and_extract_text(img_bytes):
    """Physically redacts sensitive text locally."""
    try:
        # 1. Open without converting yet (Coderabbit Fix #1)
        image = Image.open(io.BytesIO(img_bytes))
        
        # 2. Check for "Decompression Bombs" (Pixel check)
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image dimensions too large")
            
        # 3. Now convert to RGB
        image = image.convert("RGB")
        draw = ImageDraw.Draw(image)
        
        # OCR Stage
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        
        # PII Fortress
        pii_pattern = re.compile(
            r'('
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|'            
            r'(\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4,}|' 
            r'\b\d{4}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b|'            
            r'\b\d{13,19}\b'                                             
            r')',
            re.IGNORECASE
        )

        # Redaction Stage
        # 1. Filter out "Ghost" rows (structural rows like paragraphs/blocks)
        text_indices = [
            i for i in range(len(data['text'])) 
            if data['text'][i].strip() and data.get('conf', [-1]*len(data['text']))[i] != -1
        ]
        
        # 2. Build the full text using ONLY real words
        filtered_words = [data['text'][i] for i in text_indices]
        full_text = " ".join(filtered_words)
        
        # Create the list we will eventually send to the AI
        scrubbed_words = list(filtered_words) 

        # 3. Search for PII in the clean, filtered text
        for match in pii_pattern.finditer(full_text):
            start_char = match.start()
            end_char = match.end()

            current_char_pos = 0
            for idx, i in enumerate(text_indices):
                word = data['text'][i]
                word_len = len(word)
                
                # Check if this word's position in the full sentence overlaps with the PII match
                word_start = current_char_pos
                word_end = current_char_pos + word_len
                
                if word_start < end_char and word_end > start_char:
                    # Draw the rectangle using the original coordinates from Tesseract
                    x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                    draw.rectangle([x, y, x + w, y + h], fill="black")
                    scrubbed_words[idx] = "[REDACTED]"
                
                # Move position forward (including the space we added in join)
                current_char_pos += word_len + 1

        buffered = io.BytesIO()
        image.save(buffered, format="JPEG")
        redacted_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        return redacted_b64, " ".join(scrubbed_words)
        
    except Exception as e:
        print(f"⚠️ Redaction Error: {e}")
        raise ValueError(str(e)) from e

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

# --- THE ANALYZE ENDPOINT (Now Fully Async + Robust JSON Parsing) ---
@app.post("/analyze")
async def analyze(payload: dict, request: Request):
    b64 = payload.get("base64")
    if not b64:
        raise HTTPException(status_code=400, detail="Missing image data")

    # Rate Limiting
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    history = request_history.get(client_ip, [])
    history = [t for t in history if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(history) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many requests")
    history.append(now)
    request_history[client_ip] = history

    try:
        # 1. Strict Base64 Check
        try:
            img_bytes = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid base64 image data") from None

        if len(img_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="Image too large (max 5MB)")

        # 2. Offload OCR to a thread so it doesn't freeze the app
        try:
            safe_b64, extracted_text = await asyncio.to_thread(scrub_image_and_extract_text, img_bytes)
        except UnidentifiedImageError:
            raise HTTPException(status_code=415, detail="Corrupt or invalid image file") from None
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e)) from e

        ocr_context = extracted_text if extracted_text.strip() else "[No text detected]"

        # 3. Use Async HTTP for AI calls
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {NOVA_API_KEY}", "Content-Type": "application/json"}
            
            # --- CALL 1: THE DETECTIVE ---
            resp1 = await client.post(
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
                headers=headers, timeout=25.0
            )
            resp1.raise_for_status()
            raw_det = resp1.json()["choices"][0]["message"]["content"]
            
            # Robust JSON Parsing for Detective
            try:
                start_det = raw_det.find('{')
                end_det = raw_det.rfind('}') + 1
                if start_det == -1 or end_det == 0:
                    raise ValueError("No JSON object found in response")
                analysis_data = json.loads(raw_det[start_det:end_det])
            except Exception:
                print(f"🕵️ Detective failed to give JSON. Raw: {raw_det}")
                raise ValueError("Detective response was not valid JSON") from None
                
            print(f"🕵️ DETECTIVE: {analysis_data}")

            # --- CALL 2: THE GRANDCHILD ---
            resp2 = await client.post(
                "https://api.nova.amazon.com/v1/chat/completions",
                json={
                    "model": "nova-2-lite-v1",
                    "messages": [
                        {"role": "system", "content": STEP2_EMPATHY_PROMPT},
                        {"role": "user", "content": f"Findings: {json.dumps(analysis_data)}"}
                    ],
                    "temperature": 0.0
                },
                headers=headers, timeout=30.0
            )
            resp2.raise_for_status()
            raw_emp = resp2.json()["choices"][0]["message"]["content"]
            
            # Robust JSON Parsing for Grandchild
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
        # Pass through FastAPI exceptions (like rate limits and file size errors)
        raise
    except Exception as e:
        import traceback
        print(f"🚨 PIPELINE ERROR: {e}")
        traceback.print_exc() 
        return {
            "category": "caution",
            "is_ai": False,
            "scam_probability": 50,
            "title": "🔌 Connection Error",
            "grandma_reply": "❤️ Nana, my brain is having trouble connecting. Please try again in a moment ❤️"
        }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("🚀 Not My Nana — Protected Multi-Step Mode (Async Edition)")
    uvicorn.run(app, host="0.0.0.0", port=port)
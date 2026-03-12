import os
import re
import io
import json
import base64
import time
import asyncio
import random
import httpx
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

load_dotenv()

# --- WINDOWS TESSERACT CONFIG ---
if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = os.getenv(
        'TESSERACT_CMD', 
        r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    )

NOVA_API_KEY = os.getenv("NOVA_API_KEY")

if not NOVA_API_KEY:
    print("❌ ERROR: NOVA_API_KEY is missing!")
    raise RuntimeError("NOVA_API_KEY environment variable is missing")

app = FastAPI(title="Not My Nana ❤️")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- THE SECURITY & RATE LIMITING CONSTANTS ---
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_OCR_CONTEXT_CHARS = 4000
RATE_LIMIT_REQUESTS = 15
RATE_LIMIT_WINDOW_SECONDS = 60
request_history = TTLCache(maxsize=1024, ttl=RATE_LIMIT_WINDOW_SECONDS)

class ImageTooLargeError(ValueError):
    pass
# --- THE PII SCRUBBER & OCR ---
def scrub_image_and_extract_text(img_bytes):
    """Physically redacts sensitive text locally — word by word."""
    try:
        # 1. Open & basic safety
        image = Image.open(io.BytesIO(img_bytes))
        image.verify()  # quick corruption check
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")  # reopen after verify

        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ImageTooLargeError("Image dimensions too large")

        draw = ImageDraw.Draw(image)

        # 2. OCR
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        # 3. Only consider "real" words with decent confidence
        confidences = data.get('conf', [100] * len(data['text'])) # Fallback to 100 if missing
        text_indices = [
            i for i in range(len(data['text']))
            if data['text'][i].strip() and confidences[i] >= 60
        ]

        scrubbed_words = [data['text'][i] for i in text_indices]

        # 4. Best regex we can reasonably use here (catches most real-world phones + emails)
        pii_pattern = re.compile(
            r'('
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|'               # emails
            r'(\+?\d{1,3}[-.\s()]*\d{1,4}[-.\s()]*\d{3,4}[-.\s()]*\d{4,})|'  # flexible phones
            r'\b\d{4}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b|'                 # credit-card like
            r'\b\d{13,19}\b'                                                  # long numbers
            r')',
            re.IGNORECASE | re.UNICODE
        )

        # 5. Redact word-by-word
        for idx, word_idx in enumerate(text_indices):
            word = data['text'][word_idx].strip()
            if pii_pattern.search(word):
                x = data['left'][word_idx]
                y = data['top'][word_idx]
                w = data['width'][word_idx]
                h = data['height'][word_idx]
                # Slightly larger box = less chance of leaking edges
                draw.rectangle([x-5, y-5, x + w + 5, y + h + 5], fill="black")
                scrubbed_words[idx] = "[REDACTED]"

        # 6. Save redacted image
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG", quality=85)  # smaller size, good enough
        redacted_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        return redacted_b64, " ".join(scrubbed_words)

    except (UnidentifiedImageError, OSError, ImageTooLargeError):
        raise
    except Exception as e:
        print(f"⚠️ Redaction Error: {e}")
        raise RuntimeError("PII redaction failed") from e

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

# --- ASYNC RETRY HELPER ---
async def fetch_with_retries(client: httpx.AsyncClient, url: str, json_data: dict, headers: dict, timeout: float, max_retries: int = 3):
    """Fetches from API with exponential backoff for transient network/5xx errors."""
    for attempt in range(max_retries):
        try:
            resp = await client.post(url, json=json_data, headers=headers, timeout=timeout)
            resp.raise_for_status()  # Raises for any 4xx/5xx
            return resp
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            # If it's a 4xx error (like 400 Bad Request or 401 Unauthorized), do NOT retry.            
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                raise
            
            if attempt == max_retries - 1:
                raise # Out of retries, fail completely
            
            # Exponential backoff with jitter (e.g., ~1.2s, ~2.5s)
            sleep_time = (2 ** attempt) + random.uniform(0, 1)
            print(f"⚠️ API Network Error ({e}). Retrying in {sleep_time:.2f}s... (Attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(sleep_time)

# --- THE ANALYZE ENDPOINT ---
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
        except (UnidentifiedImageError, OSError):
            raise HTTPException(status_code=415, detail="Corrupt or invalid image file") from None
        except ImageTooLargeError as e:
            raise HTTPException(status_code=413, detail=str(e)) from e

        ocr_context = extracted_text.strip() or "[No text detected]"
        if len(ocr_context) > MAX_OCR_CONTEXT_CHARS:
            ocr_context = ocr_context[:MAX_OCR_CONTEXT_CHARS] + " …[truncated]"

        # 3. Use Async HTTP for AI calls
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {NOVA_API_KEY}", "Content-Type": "application/json"}
            
            # --- CALL 1: THE DETECTIVE ---
            resp1 = await fetch_with_retries(
                client,
                "https://api.nova.amazon.com/v1/chat/completions",
                json_data={
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
            resp1_data = resp1.json()
            try:
                raw_det = resp1_data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                print("🕵️ Unexpected detective response structure")
                raise ValueError("Detective API returned unexpected response format") from e
            
            # Robust JSON Parsing for Detective
            try:                
                start_idx = raw_det.find('{')
                end_idx   = raw_det.rfind('}') + 1

                if start_idx == -1 or end_idx <= start_idx:                    
                    print(f"JSON not found in DEtective: {raw_det[:200]!r}")
                    raise ValueError("Detective missing JSON")

                json_str = raw_det[start_idx:end_idx]
                analysis_data = json.loads(json_str)

            except (json.JSONDecodeError, ValueError) as e:
                print(f"JSON parse failed: {e} — raw: {raw_det[:300]!r}")
                # Optional: return fallback result instead of crashing whole request
                analysis_data = {
                    "category": "caution",
                    "is_ai": False,
                    "ai_score": 0,
                    "scam_probability": 60,
                    "dominant_language": "en",
                    "technical_findings": ["Could not understand AI answer"],
                    "title": "🤔 Hmm...",
                    "grandma_reply": "❤️ Nana, I got confused by the answer. Try again in a minute? ❤️"
                }
                
            print("🕵️ Detective analysis parsed")

            # --- CALL 2: THE GRANDCHILD ---
            resp2 = await fetch_with_retries(
                client,
                "https://api.nova.amazon.com/v1/chat/completions",
                json_data={
                    "model": "nova-2-lite-v1",
                    "messages": [
                        {"role": "system", "content": STEP2_EMPATHY_PROMPT},
                        {"role": "user", "content": f"Findings: {json.dumps(analysis_data)}"}
                    ],
                    "temperature": 0.0
                },
                headers=headers, timeout=30.0
            )            
            resp2_data = resp2.json()
            try:
                raw_emp = resp2_data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                print("❤️ Unexpected grandchild response structure")
                raise ValueError("Grandchild API returned unexpected response format") from e
            
            # Robust JSON Parsing for Grandchild
            try:                
                start_idx = raw_emp.find('{')
                end_idx   = raw_emp.rfind('}') + 1

                if start_idx == -1 or end_idx <= start_idx:                    
                    print(f"JSON not found in Grandchild: {raw_emp[:200]!r}")
                    raise ValueError("Grandchild missing JSON")

                json_str = raw_emp[start_idx:end_idx]
                empathy_data = json.loads(json_str)

            except (json.JSONDecodeError, ValueError) as e:
                print(f"JSON parse failed: {e} — raw: {raw_emp[:300]!r}")
                # Optional: return fallback result instead of crashing whole request
                empathy_data = {
                    "category": "caution",
                    "is_ai": False,
                    "ai_score": 0,
                    "scam_probability": 60,
                    "dominant_language": "en",
                    "technical_findings": ["Could not understand AI answer"],
                    "title": "🤔 Hmm...",
                    "grandma_reply": "❤️ Nana, I got confused by the answer. Try again in a minute? ❤️"
                }    
            print("❤️ Grandchild response parsed")

        allowed_categories = {"scam", "ai_image", "sensitive", "viral", "safe"}
        category = analysis_data.get("category")
        if category not in allowed_categories:
            category = "caution"

        return {
            "category": category,
            "is_ai": analysis_data.get("is_ai", False),
            "ai_score": analysis_data.get("ai_score", 0),
            "scam_probability": analysis_data.get("scam_probability", 0),
            "dominant_language": analysis_data.get("dominant_language", "en"),
            "technical_findings": analysis_data.get("technical_findings", []),
            "title": empathy_data.get("title", "Check Results"),
            "grandma_reply": empathy_data.get("grandma_reply", "Something went wrong. ❤️")
        }

    except HTTPException:
        # Pass through FastAPI exceptions (like rate limits and file size errors)
        raise
    except Exception:
        import traceback
        print("🚨 PIPELINE ERROR")
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
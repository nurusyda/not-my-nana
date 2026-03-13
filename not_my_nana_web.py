import os
import re
import io
import json
import base64
import time
import asyncio
import random
import httpx
import logging
import binascii
import uuid
from PIL import Image, ImageDraw, UnidentifiedImageError
import pytesseract
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from cachetools import TTLCache

from prompts import STEP1_ANALYSIS_PROMPT, STEP2_EMPATHY_PROMPT

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("NotMyNana")

load_dotenv()

# --- WINDOWS TESSERACT CONFIG ---
if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = os.getenv(
        'TESSERACT_CMD', 
        r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    )

NOVA_API_KEY = os.getenv("NOVA_API_KEY")
if not NOVA_API_KEY:
    logger.critical("❌ NOVA_API_KEY is missing from environment variables!")
    raise RuntimeError("NOVA_API_KEY environment variable is missing")

app = FastAPI(title="Not My Nana ❤️")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONSTANTS ---
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_OCR_CONTEXT_CHARS = 4000
RATE_LIMIT_REQUESTS = 15
RATE_LIMIT_WINDOW_SECONDS = 60
request_history = TTLCache(maxsize=1024, ttl=RATE_LIMIT_WINDOW_SECONDS)

class ImageTooLargeError(ValueError):
    pass

# --- PII SCRUBBER & OCR ---
def scrub_image_and_extract_text(img_bytes):
    """Redacts PII by scanning the full text stream to catch multi-token spans."""
    try:
        image = Image.open(io.BytesIO(img_bytes))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ImageTooLargeError("Image dimensions too large")
        
        image = image.convert("RGB")
        draw = ImageDraw.Draw(image)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        # 1. Gather stats and valid indices
        valid_indices = [i for i, word in enumerate(data["text"]) if word and word.strip()]
        ocr_word_count = len(valid_indices)
        conf_values = [float(c) for c in data['conf'] if float(c) >= 0]
        avg_conf = sum(conf_values) / len(conf_values) if conf_values else 0.0

        # 2. Build searchable text and character-to-box mapping
        full_text = ""
        char_to_word = []
        for i in valid_indices:
            word = data["text"][i]
            for _ in word:
                char_to_word.append(i)
            full_text += word + " "
            char_to_word.append(-1)  # Space mapping

        pii_pattern = re.compile(
            r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})|' 
            r'(\+?\d{1,3}[-.\s()]*\d{1,4}[-.\s()]*\d{3,4}[-.\s()]*\d{4,})|' 
            r'(\b\d{4}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b)',
            re.IGNORECASE
        )

        scrubbed_words = list(data['text'])
        redaction_count = 0
        redacted_types = set()
        match_count = 0

        # 3. Process matches
        for match in pii_pattern.finditer(full_text):
            match_count += 1
            start, end = match.span()

            # Classify type via capture groups
            email, phone, card = match.groups()
            if email: redacted_types.add("email")
            elif phone: redacted_types.add("phone")
            elif card: redacted_types.add("credit_card")

            # Identify which OCR boxes correspond to this text span
            affected_indices = {idx for idx in char_to_word[start:end] if idx != -1}
            for idx in affected_indices:
                x, y, w, h = data['left'][idx], data['top'][idx], data['width'][idx], data['height'][idx]
                
                # Draw black rectangle with 2px safety padding
                draw.rectangle([x-2, y-2, x+w+2, y+h+2], fill="black")
                scrubbed_words[idx] = "[REDACTED]"
                redaction_count += 1

        # 4. Final logging
        if redaction_count > 0:
            logger.info(
                "PII Redacted: %d pattern matches (%d boxes) | Types: %s",
                match_count, redaction_count, ", ".join(sorted(redacted_types))
            )
        else:
            logger.debug(
                "No PII detected | OCR words: %d | Avg Conf: %.1f%%",
                ocr_word_count, avg_conf
            )

        buffered = io.BytesIO()
        image.save(buffered, format="JPEG", quality=85)
        return base64.b64encode(buffered.getvalue()).decode("utf-8"), " ".join(scrubbed_words)

    except (UnidentifiedImageError, ImageTooLargeError):
        raise
    except Exception as e:
        logger.error(f"PII Redaction Error: {e}")
        raise RuntimeError("PII redaction failed") from e

# --- ROUTES ---
@app.get("/health")
async def health():
    """Standard health check for container orchestration."""
    return {"status": "healthy", "timestamp": time.time()}

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
    """Hardened retry logic to split connection issues from billing risks."""
    for attempt in range(max_retries):
        try:
            resp = await client.post(url, json=json_data, headers=headers, timeout=timeout)
            
            if resp.status_code >= 500 and attempt < max_retries - 1:
                sleep_time = 1 + random.random() * (2 ** attempt)
                logger.warning(f"⚠️ Server Error {resp.status_code}. Retrying in {sleep_time:.2f}s...")
                await asyncio.sleep(sleep_time)
                continue
            
            resp.raise_for_status()
            return resp

        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            if attempt == max_retries - 1:
                logger.error(f"API unreachable after {max_retries} attempts: {e}")
                raise
            sleep_time = 1 + random.random() * (2 ** attempt)
            logger.warning(f"📡 Connection failed ({type(e).__name__}). Retrying in {sleep_time:.2f}s...")
            await asyncio.sleep(sleep_time)

        except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
            logger.error(f"🚫 Timeout after request sent ({type(e).__name__}). Failing fast to avoid double-billing.")
            raise

        except httpx.HTTPStatusError as e:
            logger.error(f"❌ Client Error ({e.response.status_code}). No retry.")
            raise
            
        except Exception as e:
            logger.exception(f"🚨 Unexpected retry error: {type(e).__name__}")
            raise

# --- CORE PIPELINE ---
@app.post("/analyze")
async def analyze(payload: dict, request: Request):
    b64 = payload.get("base64")
    if not b64:
        raise HTTPException(status_code=400, detail="Missing image data")

    # Rate Limiting Logic
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    history = request_history.get(client_ip, [])
    history = [t for t in history if now - t < RATE_LIMIT_WINDOW_SECONDS]
    
    if len(history) >= RATE_LIMIT_REQUESTS:
        logger.warning(f"Rate limit hit for {client_ip}")
        return JSONResponse(status_code=429, content={
            "title": "☕ Time for a Tea Break?",
            "grandma_reply": "❤️ Nana, I've been thinking a lot lately! Let's take a tiny break for a minute and then we can check more pictures together. ❤️"
        })
    
    history.append(now)
    request_history[client_ip] = history

    fallback_response = {
        "category": "caution",
        "title": "🔌 Connection Error",
        "grandma_reply": "❤️ Nana, my brain is having trouble connecting. Please try again in a moment! ❤️"
    }

    try:
        # 1. Validation & OCR
        try:
            img_bytes = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as err:
            raise HTTPException(status_code=400, detail="Invalid base64 image data") from err

        if len(img_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="Image too large (max 5MB)")

        try:
            safe_b64, extracted_text = await asyncio.to_thread(scrub_image_and_extract_text, img_bytes)
        except ImageTooLargeError as e:
            raise HTTPException(status_code=413, detail=str(e)) from e
        except UnidentifiedImageError as e:
            raise HTTPException(status_code=415, detail="Unsupported or corrupt image") from e

        ocr_context = extracted_text.strip() or "[No text detected]"
        if len(ocr_context) > MAX_OCR_CONTEXT_CHARS:
            ocr_context = ocr_context[:MAX_OCR_CONTEXT_CHARS] + " …[truncated]"

        # 2. AI Detective & Empathy Pipeline
        idempotency_key = str(uuid.uuid4())
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {NOVA_API_KEY}",
                "Content-Type": "application/json"
            }
            if os.getenv("USE_IDEMPOTENCY", "true").lower() == "true":
                headers["X-Idempotency-Key"] = idempotency_key

            # Call 1: Technical Analysis (Detective)
            resp1 = await fetch_with_retries(
                client, "https://api.nova.amazon.com/v1/chat/completions",
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
                headers=headers,
                timeout=25.0
            )
            
            resp1_data = resp1.json()
            choices = resp1_data.get("choices", [])
            if not choices or "message" not in choices[0] or "content" not in choices[0]["message"]:
                logger.error("Unexpected API response structure from Detective")
                raise ValueError("Detective API returned unexpected response format")
            raw_det = choices[0]["message"]["content"]
            s1, e1 = raw_det.find('{'), raw_det.rfind('}') + 1
            if s1 == -1 or e1 <= s1:
                logger.error("No JSON object found in Detective response")
                raise ValueError("Detective response was not valid JSON")
            analysis_data = json.loads(raw_det[s1:e1])

            # Call 2: Friendly Translation (Grandchild)
            resp2 = await fetch_with_retries(
                client, "https://api.nova.amazon.com/v1/chat/completions",
                json_data={
                    "model": "nova-2-lite-v1",
                    "messages": [
                        {"role": "system", "content": STEP2_EMPATHY_PROMPT},
                        {"role": "user", "content": f"Findings: {json.dumps(analysis_data)}"}
                    ],
                    "temperature": 0.0
                },
                headers=headers,
                timeout=30.0
            )
            
            resp2_data = resp2.json()
            choices = resp2_data.get("choices", [])
            if not choices or "message" not in choices[0] or "content" not in choices[0]["message"]:
                logger.error("Unexpected API response structure from Grandchild")
                raise ValueError("Grandchild API returned unexpected response format")
            raw_emp = choices[0]["message"]["content"]
            s2, e2 = raw_emp.find('{'), raw_emp.rfind('}') + 1
            if s2 == -1 or e2 <= s2:
                logger.error("No JSON object found in Grandchild response")
                raise ValueError("Grandchild response was not valid JSON")
            empathy_data = json.loads(raw_emp[s2:e2])

        ALLOWED_CATEGORIES = {"scam", "ai_image", "sensitive", "viral", "safe", "caution"}
        category = analysis_data.get("category", "caution")
        if category not in ALLOWED_CATEGORIES:
            logger.warning(f"Unknown category from AI: {category}")
            category = "caution"

        return {
            "category": category,
            "is_ai": analysis_data.get("is_ai", False),
            "dominant_language": analysis_data.get("dominant_language", "en"),
            "title": empathy_data.get("title", "Check Results"),
            "grandma_reply": empathy_data.get("grandma_reply", "Something went wrong. ❤️")
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error {e.response.status_code}: {e}")
        return JSONResponse(status_code=e.response.status_code, content=fallback_response)
    except httpx.RequestError as e:
        logger.error(f"Network request failed: {e}")
        return JSONResponse(status_code=503, content=fallback_response)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode failed in pipeline: {e}")
        return JSONResponse(status_code=500, content=fallback_response)
    except Exception:
        logger.exception("Unexpected pipeline error")
        return JSONResponse(status_code=500, content=fallback_response)
        
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"🚀 Not My Nana starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)

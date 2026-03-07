import os
import json
import requests
import base64
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from cachetools import TTLCache


from prompts import SYSTEM_PROMPT, FEW_SHOT_EXAMPLES 

# Security constants
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
RATE_LIMIT_REQUESTS = 15
RATE_LIMIT_WINDOW_SECONDS = 60
request_history = TTLCache(maxsize=1024, ttl=RATE_LIMIT_WINDOW_SECONDS)

load_dotenv()
NOVA_API_KEY = os.getenv("NOVA_API_KEY")

if not NOVA_API_KEY:
    raise RuntimeError("NOVA_API_KEY environment variable is missing")

app = FastAPI(title="Not My Nana ❤️")
templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")

# Manifest 
@app.get("/manifest.json")
async def manifest():
    """Generates the PWA manifest.json file for app installation."""
    return JSONResponse({
            "name": "Not My Nana",
            "short_name": "Not My Nana",
            "description": "Protecting our loved ones from digital scams and fearmongering.",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#c45c5c", 
            "icons": [
                {
                    "src": "/static/logo-192.png", 
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable"
                },
                {
                    "src": "/static/logo-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable"
                }
            ]
        })  

# Home page 
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Renders the main home page template."""
    return templates.TemplateResponse("index.html", {"request": request})

# Analyze endpoint
@app.post("/analyze")
async def analyze(payload: dict, request: Request):
    """Accepts a base64 encoded image and passes it to the AI model to check for scams."""
    b64 = payload.get("base64")
    mime = payload.get("mime", "jpeg")

    # Security checks 
    if not b64:
        raise HTTPException(status_code=400, detail="Missing base64 data")
    try:
        img_bytes = base64.b64decode(b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 data")

    if len(img_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 5MB)")

    allowed_mimes = {"jpeg", "jpg", "png", "gif", "webp"}
    if mime.lower() not in allowed_mimes:
        raise HTTPException(status_code=415, detail="Unsupported image format")

    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    history = request_history.get(client_ip, [])
    history = [t for t in history if now - t < RATE_LIMIT_WINDOW_SECONDS]
    
    if len(history) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many requests — please wait a minute")
    
    history.append(now)
    request_history[client_ip] = history

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Analyze this screenshot.\n{FEW_SHOT_EXAMPLES}\nLook closely... Output ONLY JSON."},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}}
            ]
        }
    ]

    try:
        resp = requests.post(
            "https://api.nova.amazon.com/v1/chat/completions",
            json={"model": "nova-2-lite-v1", "messages": messages, "max_tokens": 600, "temperature": 0.0},
            headers={"Authorization": f"Bearer {NOVA_API_KEY}", "Content-Type": "application/json"},
            timeout=(10, 35)
        )
        resp.raise_for_status()

        raw = resp.json()["choices"][0]["message"]["content"]
        clean = raw.split("```json")[-1].split("```")[0].strip() if "```" in raw else raw

        try:
            result = json.loads(clean)
            print(f"✅ Analyzed | scam={result.get('scam_probability')} | lang=auto | ip={request.client.host}")
        except json.JSONDecodeError:
            result = {
                "scam_probability": 50,
                "grandma_reply": "❤️ Nana, I got a bit confused by that picture... Could you try again or show me a clearer one? ❤️"
            }

        return result

    except Exception as e:
        print(f"Error in /analyze: {type(e).__name__}")
        return {
            "scam_probability": 50,
            "fallback": True, 
            "error_detail": str(e),
            "grandma_reply": "❤️ Nana, something went wrong... Please try again in a moment ❤️"
        }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("🚀 Not My Nana — Clean One-Button Gallery Only!")
    uvicorn.run(app, host="0.0.0.0", port=port)






import os
import json
import requests
import base64
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from collections import defaultdict
import time

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
RATE_LIMIT_REQUESTS = 15
RATE_LIMIT_WINDOW_SECONDS = 60

request_history = defaultdict(list)

load_dotenv()
NOVA_API_KEY = os.getenv("NOVA_API_KEY")

if not NOVA_API_KEY:
    raise RuntimeError("NOVA_API_KEY environment variable is missing")

app = FastAPI(title="Not My Nana ❤️")

@app.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name": "Not My Nana ❤️",
        "short_name": "Not My Nana",
        "description": "Protect Nana from scams — one tap!",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#fff9e6",
        "theme_color": "#ff9800",
        "icons": [
            {"src": "https://via.placeholder.com/192/FF9800/FFFFFF?text=Nana", "sizes": "192x192", "type": "image/png"},
            {"src": "https://via.placeholder.com/512/FF9800/FFFFFF?text=Nana", "sizes": "512x512", "type": "image/png"}
        ],
        "share_target": {
            "action": "/analyze_shared",
            "method": "POST",
            "enctype": "multipart/form-data",
            "params": {"files": [{"name": "file", "accept": ["image/*"]}]}
        }
    })

@app.get("/")
async def home():
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Not My Nana ❤️</title>
        <link rel="manifest" href="/manifest.json">
        <style>
            body { font-family: system-ui; background: #fff9e6; color: #333; text-align: center; margin: 0; padding: 20px 15px; min-height: 100vh; }
            h1 { font-size: 48px; color: #d32f2f; margin: 15px 0 5px; }
            button { font-size: 28px; padding: 22px; margin: 15px auto; border: none; border-radius: 25px; width: 90%; max-width: 420px; cursor: pointer; font-weight: bold; background: #ff9800; color: white; display: block; }
            .result { margin: 30px auto; padding: 28px; border-radius: 22px; font-size: 27px; line-height: 1.4; max-width: 420px; box-shadow: 0 6px 20px rgba(0,0,0,0.1); }
            .scam { background: #f44336; color: white; }
            .safe { background: #4caf50; color: white; }
            .caution { background: #2196f3; color: white; }
            #loading { display:none; font-size: 28px; color:#ff9800; margin: 30px 0; }
        </style>
    </head>
    <body>
        <h1>❤️ Not My Nana</h1>

        <button onclick="document.getElementById('file').click()">📸 Choose Screenshot from Gallery</button>
        <input type="file" id="file" accept="image/*" style="display:none" onchange="uploadFile(this)">

        <div id="loading">Thinking for Nana... ❤️</div>
        <div id="result"></div>

            <script>
                async function uploadFile(input) {
                    const file = input.files[0];
                    if (!file) return;
            
                    if (file.size > 5 * 1024 * 1024) {
                        alert("Sorry Nana, this picture is too big (max 5MB)");
                        return;
                    }
            
                    const reader = new FileReader();
                    reader.onload = async function(e) {
                        try {
                            const b64 = e.target.result.split(',')[1];
                            const mime = file.type.split('/')[1] || 'jpeg';
            
                            document.getElementById('loading').style.display = 'block';
                            document.getElementById('result').innerHTML = '';
            
                            const res = await fetch('/analyze', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({base64: b64, mime: mime})
                            });
            
                            if (!res.ok) throw new Error(`Server error: ${res.status}`);
            
                            const data = await res.json();
            
                            document.getElementById('loading').style.display = 'none';
            
                            const isScam = data.scam_probability > 80;
                            const isCaution = data.scam_probability >= 60 && data.scam_probability <= 80;
                            let className = isScam ? 'scam' : (isCaution ? 'caution' : 'safe');
                            let title = isScam ? '🚨 SCAM ALERT!' : (isCaution ? '🤔 Let’s Talk About This' : '✅ Looks safe, Nana!');
            
                            const html = `
                                <div class="result ${className}">
                                    <strong>${title}</strong><br><br>
                                    ${data.grandma_reply}
                                </div>
                            `;
                            document.getElementById('result').innerHTML = html;
            
                        } catch (err) {
                            document.getElementById('loading').style.display = 'none';
                            document.getElementById('result').innerHTML = `
                                <div class="result caution">
                                    ❤️ Nana, something went wrong... Please try a smaller picture or wait a moment ❤️
                                </div>
                            `;
                            console.error(err);
                        }
                    };
                    reader.readAsDataURL(file);
                }
            </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.post("/analyze")
async def analyze(payload: dict, request: Request):
    b64 = payload.get("base64")
    mime = payload.get("mime", "jpeg")

    # Security checks first
    if not b64:
        raise HTTPException(status_code=400, detail="Missing base64 data")
    if len(b64) > MAX_IMAGE_SIZE_BYTES * 2:
        raise HTTPException(status_code=413, detail="Image too large (max ~5MB)")
    try:
        img_bytes = base64.b64decode(b64)
        if len(img_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="Decoded image too large (max 5MB)")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 data")

    allowed_mimes = {"jpeg", "jpg", "png", "gif", "webp"}
    if mime.lower() not in allowed_mimes:
        raise HTTPException(status_code=415, detail="Unsupported image format")

    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    history = request_history[client_ip]
    history = [t for t in history if now - t < RATE_LIMIT_WINDOW_SECONDS]
    history.append(now)
    request_history[client_ip] = history
    if len(history) > RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many requests — please wait a minute")

    # ── Your prompt and Nova call ──
    few_shot = """Examples:
1. Fake Amazon prize ("Congratulations! You won $500...") -> 95, "❤️ Nana, this is a classic scam! Don't click anything, just delete and block."
2. Real grandkids photo -> 5, "❤️ Nana, that's such a lovely picture! Makes my heart happy."
3. "Hi Mom" from unknown -> 70, "❤️ Nana, better safe than sorry, don't reply to strange numbers."
4. Son asking for money on new number -> 92, "❤️ Nana, this is a trick! Call him on his real number to check."
5. Bank "account locked, click here" -> 96, "❤️ Nana, never click these! Call the number on the back of your card instead."
6. Fake birthday e-card download -> 88, "❤️ Nana, real invitations don't ask you to download strange files."
7. Normal family message -> 8, "❤️ Nana, sweet message — no scam here!" 
8. Screenshot of AI deepfake video call (wrong shadows, waxy skin) -> 94, "❤️ Nana, oh no, this is NOT your son! The shadows don't match and his skin looks like plastic. It's a fake video! Hang up."
9. Bombastic fake disaster (giant tornado, unrealistic bombs/fire, strange physics) -> 98, "❤️ Nana, don't let this scare you! It's a fake computer picture meant to cause panic. Notice how the fire looks like painting? It's not real."
10. Unrealistic animal scenario (tiny kitten fighting a bear, animal with 5 legs) -> 95, "❤️ Nana, look closely at the paws! Computers make these fake animal videos to get 'likes' on the internet. It's totally fake."
11. Tech Support Pop-up (Virus detected, call this number) -> 99, "❤️ Nana, don't call them! Microsoft or Apple will never put a phone number on your screen like that. Close the window immediately!"
12. Romance/Military Scam ("Hello beautiful, I am deployed in Syria and need iTunes cards") -> 98, "❤️ Nana, this is a romance scam! Real soldiers don't ask strangers for gift cards. Please block this person."
13. Fake Celebrity/Crypto Investment ("Elon Musk is giving back, click here to double your money") -> 97, "❤️ Nana, celebrities don't give away money like this online. It's a trick to steal your bank details!"
14. Miracle Health Cure ("One gummy reverses arthritis overnight") -> 92, "❤️ Nana, if it sounds too good to be true, it is. Always check with your real doctor before buying medicine online."
15. Government/Medicare Impersonator ("Your Social Security number has been suspended") -> 96, "❤️ Nana, the government will NEVER text or call you to say your number is suspended. Hang up and ignore!"
16. Fake news disaster photo ("Massive earthquake in Florida right now!") -> 97, "❤️ Nana, this photo is fake! Earthquakes don't look like that and the timestamp is wrong. Don't forward it."
17. Celebrity death hoax ("Taylor Swift just passed away 😭") -> 96, "❤️ Nana, this is a cruel lie! Taylor Swift is alive and well. These fake death posts are everywhere."
18. Viral chain message ("Forward this to 10 people or bad luck will come") -> 85, "❤️ Nana, these chain letters are old tricks. Just delete — nothing bad will happen if you don't forward."
19. Doctored political/news screenshot ("President says free money tomorrow!") -> 94, "❤️ Nana, this headline is fake. Real news never promises free money like that. Check a trusted site."
20. Fake local crime alert ("Burglars using AirTags in your neighborhood!") -> 92, "❤️ Nana, this is a viral hoax. Police never send messages like this. Ignore and stay safe ❤️" """

    messages = [
        {
            "role": "system",
            "content": """You are Not My Nana — a loving, protective grandma AI ❤️. 

You protect elderly users from scams and fake content.

IMPORTANT RULES:
1. First, detect the main language in the screenshot text and ALWAYS reply in that exact same language.
2. Read the FULL context and meaning of the image — do NOT react to single words like "America", "war", or "politics" alone.
3. If it's mild fearmongering or silly fake news (e.g. "America will collapse tomorrow" with no real backing) → give a calm, reassuring note with a light fact.
4. Only use caution mode (scam_probability 60-75) if the content is truly divisive, political, religious, war-related, or could cause real family arguments.
5. Keep every reply warm, simple, with big feelings and emojis.

Output ONLY valid JSON: {"scam_probability": number, "grandma_reply": "message"}"""
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Analyze this screenshot.\n{few_shot}\nLook closely for scams, AI artifacts, or fake news. Output ONLY JSON."},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}}
            ]
        }
    ]

    try:
        resp = requests.post(
            "https://api.nova.amazon.com/v1/chat/completions",
            json={"model": "nova-2-lite-v1", "messages": messages, "max_tokens": 600, "temperature": 0.1},
            headers={"Authorization": f"Bearer {NOVA_API_KEY}", "Content-Type": "application/json"},
            timeout=(10, 35)
        )
        resp.raise_for_status()

        raw = resp.json()["choices"][0]["message"]["content"]
        clean = raw.split("```json")[-1].split("```")[0].strip() if "```" in raw else raw

        try:
            result = json.loads(clean)
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
            "grandma_reply": "❤️ Nana, something went wrong... Please try again in a moment ❤️"
        }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("🚀 Not My Nana — Clean One-Button Gallery Only!")
    uvicorn.run(app, host="0.0.0.0", port=port)




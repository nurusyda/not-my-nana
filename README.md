# Not My Nana ❤️
Submitted to the Amazon Nova AI Hackathon 2025/2026

**A lightweight, privacy-first Progressive Web App (PWA) that helps grandparents check suspicious messages and photos for scams — powered by Amazon Nova 2 Lite.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Built with Amazon Nova](https://img.shields.io/badge/Powered%20by-Amazon%20Nova%202%20Lite-orange?logo=amazonaws)](https://aws.amazon.com/nova/)
[![PWA Ready](https://img.shields.io/badge/PWA-Installable-success)](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)

<p align="center">
  <a href="https://not-my-nana-production.up.railway.app/">
    <img src="https://img.shields.io/badge/Launch%20Not%20My%20Nana%20%E2%9D%A4%EF%B8%8F-orange?style=for-the-badge&logo=rocket&logoColor=white" alt="Launch App">
  </a>
</p>

**Try it live →** Tap the big orange button above (or [click here](https://not-my-nana-production.up.railway.app/))  
Add it to your home screen on phone for the best experience — 4 taps to check any suspicious message or photo.

Every day, elderly loved ones face fake wedding invites, urgent bank alerts, OTP requests, viral fear posts, and prize scams — often powered by AI-generated images. One wrong tap can cost everything.

**Not My Nana** lets them screenshot anything suspicious, get a color-coded safety check + warm 2-sentence advice in 4 taps — privately, locally, and without any login.

Built for the Amazon Nova AI Hackathon — and for real families like mine.

## Why This Exists

My parents and relatives kept getting hit with these messages directly. Adult children often blame elders for falling for it — but we're rarely there in real time, and many feel too ashamed to ask. Gentle corrections can make them stop sharing entirely.

Elder fraud losses reported to the FTC skyrocketed to **$2.4 billion in 2024** (4× from $600M in 2020), with real losses possibly up to **$81.5 billion** due to underreporting ([FTC Protecting Older Consumers 2024–2025 report, Dec 2025](https://www.ftc.gov/system/files/ftc_gov/pdf/P144400-OlderAdultsReportDec2025.pdf)).
This app empowers **them** to check privately — no judgment, just support.

## Features

- **4-tap simplicity** — Screenshot → Open app → Tap big button → Choose image → Get result in 10–25s
- **Color-coded results** with large emojis:
  - ✅ Safe / Real (green)
  - ❌ Scam! Danger (red)
  - 🤖 Fake photo (AI) (grey)
  - 👨‍👩‍👧‍👦 Talk to family (purple – sensitive topics)
  - 📰 Clickbait News (yellow – viral content)
- **Warm 2-sentence "grandchild" replies** in detected language (e.g., "🚨 Nana, this is a scam! Please don't click or send money. Call me! ❤️")
- **Strong privacy** — Local OCR + redaction blacks out phone numbers, names, cards, emails **before** cloud call. Only sanitized data hits Amazon Nova.
- **Local history** — Last 3 thumbnails stored on-device (easy delete)
- **Light/dark theme** toggle
- **Installable PWA** — Works offline-ish (caching), home-screen icon on iOS/Android
- **No account, no tracking** — Zero personal data collected

## Demo Screenshots

| Main Screen - Dark Mode      | Green – Safe Result          | Red – Scam Alert             |
|------------------------------|------------------------------|------------------------------|
| ![Main Screen - Day Mode](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Homepage_Dark_Mode.png) | ![Green – Safe Result](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Green_Safe.png) | ![Red – Scam Alert](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Red_Scam.png) |

| Grey – AI-generated          | Purple – Talk to Family      | Yellow – Clickbait / News    |
|------------------------------|------------------------------|------------------------------|
| ![Grey – AI-generated Fake](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Grey_AI_Image.png) | ![Purple – Talk to Family](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Purple_Sensitive.png) | ![Yellow – Clickbait / News](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Yellow_Viral.png) |

| History View                 | Redaction Example            | Loading - Day Mode           |
|------------------------------|------------------------------|------------------------------|
| ![History View](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/History.png) | ![Redaction Example](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Redaction_Result.png) | ![Loading - Dark Mode](https://raw.githubusercontent.com/nurusyda/not-my-nana/main/Screenshots/Loading_Day_Mode.png) |


## How It Works (Quick Tour)

1. **Local redaction** (Tesseract OCR + regex) hides PII on-device.
2. **Redacted image + text** sent to Amazon Nova 2 Lite.
3. **Two-stage prompt pipeline**:
   - Detective: Analyzes for AI artifacts, urgency, greed/fear, clickbait → outputs category, scores, language.
   - Grandchild: Rewrites into kind, supportive 2-sentence reply.
4. **Result** shown with color + emoji + text.

Nova 2 Lite's multimodal reasoning (pixels + text in one pass) makes it possible to spot subtle AI flaws and emotional manipulation reliably.

## Acknowledgments

Built with ❤️ for my family and all grandparents out there.  
Huge thanks to Amazon Bedrock & Nova 2 Lite for making powerful multimodal reasoning accessible and affordable.

## Tech Stack

- **Frontend**: HTML/CSS/JS (vanilla + minimal JS for PWA)
- **Backend**: FastAPI (Python) — hosted wherever (Vercel, Render, AWS, etc.)
- **AI**: Amazon Nova 2 Lite (multimodal chat completions)
- **OCR & Redaction**: pytesseract + PIL (local)
- **PWA**: Manifest + service worker basics
- **Dependencies**: httpx, cachetools, python-dotenv, etc. (see requirements.txt)

## Contributing

We welcome contributions to make **Not My Nana** even better and help more grandparents stay safe!

Feel free to:
- Report bugs or suggest improvements by [opening an issue](https://github.com/nurusyda/not-my-nana/issues)
- Submit pull requests with code changes, new features, better prompts, translations, or UI tweaks
- Share feedback on how the app works in real life (e.g., scam examples that were missed)

For bigger changes, please open an issue first to discuss.

Thank you for helping protect our loved ones ❤️

## Installation & Setup

### 1. Clone the repo

```bash
git clone https://github.com/nurusyda/not-my-nana.git
cd not-my-nana


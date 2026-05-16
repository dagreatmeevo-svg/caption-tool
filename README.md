# Arabic Caption Tool

Upload a video or paste a TikTok/YouTube/Instagram link — get back the same video with Arabic captions burned in.

## Prerequisites

1. **Python 3.10+** — check with `python --version`
2. **FFmpeg** — download from https://ffmpeg.org/download.html
   - Windows: extract, add the `bin/` folder to your PATH
   - Test: `ffmpeg -version` should work in terminal
3. **Groq API key** (free) — https://console.groq.com
4. **DeepSeek API key** — https://platform.deepseek.com

## Setup

```bash
cd caption-tool/backend

# Copy and fill in your API keys
copy .env.example .env

# Install dependencies
pip install -r requirements.txt
```

Edit `.env`:
```
GROQ_API_KEY=gsk_your_key_here
DEEPSEEK_API_KEY=sk-your_key_here
```

## Run

```bash
cd caption-tool/backend
uvicorn main:app --reload
```

Open your browser at: **http://localhost:8000**

## Usage

1. Paste a TikTok / YouTube / Instagram link, OR drag-and-drop a video file
2. Click **أضف الترجمة العربية**
3. Watch the progress bar — takes 30–90 seconds depending on video length
4. Download your captioned video

## How it works

```
video/link → yt-dlp downloads → FFmpeg extracts audio
→ Groq Whisper transcribes → DeepSeek translates to Arabic
→ FFmpeg burns Arabic captions into video → download
```

## Notes

- Output files are auto-deleted after 24 hours
- For videos longer than ~30 min, audio is split into chunks automatically
- Cairo font is required for Arabic rendering — place `Cairo-Regular.ttf` in `backend/fonts/`
  - Download from: https://fonts.google.com/specimen/Cairo

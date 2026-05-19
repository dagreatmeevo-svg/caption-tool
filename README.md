# Arabic Caption Tool

Upload a video or paste a TikTok, YouTube, or Instagram link and get back an MP4 with Arabic captions burned in.

## Stack

- FastAPI backend
- Static HTML/Tailwind frontend served by FastAPI
- Groq Whisper transcription
- DeepSeek Arabic translation
- FFmpeg/libass subtitle burn

## Local Setup

1. Install Python 3.10+.
2. Install FFmpeg and make sure `ffmpeg -version` works.
3. Create `backend/.env`:

```env
GROQ_API_KEY=your_groq_key
DEEPSEEK_API_KEY=your_deepseek_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_DEFAULT_QUALITY=720
```

4. Install Python dependencies:

```bash
cd backend
pip install -r requirements.txt
```

5. Run the app:

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Deployment

Railway is the best fit for this project because the app needs a long-running FastAPI server, upload/temp file handling, yt-dlp, and native FFmpeg.

This repo includes:

- `Dockerfile` - installs FFmpeg and Python dependencies.
- `railway.json` - tells Railway to use the Dockerfile and health-check `/`.
- `Procfile` - kept for platforms that use Procfile-style starts.

### Railway

1. Create a new Railway project from this GitHub repo.
2. Set these environment variables:

```env
GROQ_API_KEY=your_groq_key
DEEPSEEK_API_KEY=your_deepseek_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
LOG_LEVEL=INFO
TELEGRAM_DEFAULT_QUALITY=720
```

3. Deploy. Railway should build with the root `Dockerfile`.

If you previously set a Railway dashboard start command, remove it. The Docker image now starts with `python start.py`. If Railway requires a start command for any reason, use exactly:

```bash
python start.py
```

Do not use `cd backend && ...` as a Railway start command.

### Telegram Bot

1. Create a bot with Telegram [@BotFather](https://t.me/BotFather).
2. Copy the bot token into Railway as `TELEGRAM_BOT_TOKEN`.
3. After Railway deploys, set the webhook:

```bash
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=https://YOUR_RAILWAY_DOMAIN/telegram/webhook"
```

The bot accepts:

- A video file sent directly to the bot.
- A TikTok, YouTube, or Instagram URL sent as text.

Telegram jobs default to English source audio, Arabic captions, font size 14, emoji off, and document-first delivery. Telegram output defaults to 720p to avoid Bot API upload limits. Use `/quality 720` or `/quality 1080` in the bot to choose per chat.

### Vercel

Vercel is not the recommended primary deployment for this app because the backend needs FFmpeg and longer-running video jobs. The existing `vercel.json` can host the static frontend only, but the current simplest production setup is one Railway service serving both frontend and backend.

## Notes

- For Arabic source videos, choose `العربية` in the source-language picker so the app skips DeepSeek and preserves the original Arabic transcription.
- On Windows, subtitle burning uses Tahoma for Arabic rendering. On Railway/Linux Docker, it uses Noto Naskh Arabic from the image. The bundled Cairo font remains a fallback.
- Output files are auto-deleted after 24 hours.

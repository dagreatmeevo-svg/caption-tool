import asyncio
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend at root
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# job_id → {"status": str, "steps": [str], "error": str|None, "output": str|None}
jobs: dict[str, dict] = {}


def _valid_source_language(source_language: str) -> str:
    return source_language if source_language in {"auto", "ar", "en"} else "auto"


def _check_pipeline_keys(source_language: str):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured in .env")
    if source_language != "ar" and not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="DEEPSEEK_API_KEY is not configured in .env")


def _run_pipeline(
    job_id: str,
    video_path: str,
    font_size: int = 22,
    use_emoji: bool = False,
    source_language: str = "auto",
):
    from services.downloader import download_video
    from services.transcriber import transcribe
    from services.translator import translate_to_arabic
    from services.srt_builder import write_srt
    from services.captioner import burn_subtitles

    def step(msg: str):
        jobs[job_id]["steps"].append(msg)
        jobs[job_id]["status"] = msg

    try:
        log.info(
            "job %s: starting pipeline video=%s font_size=%s use_emoji=%s source_language=%s",
            job_id,
            video_path,
            font_size,
            use_emoji,
            source_language,
        )
        step("extracting_audio")
        transcribe_language = None if source_language == "auto" else source_language
        segments = transcribe(video_path, GROQ_API_KEY, language=transcribe_language)

        step("transcribed")
        if not segments:
            raise ValueError("No speech detected in video.")

        if source_language == "ar":
            step("building_srt")
            arabic_segments = segments
        else:
            step("translating")
            arabic_segments = translate_to_arabic(segments, DEEPSEEK_API_KEY, use_emoji=use_emoji)

        srt_path = str(TEMP_DIR / f"{job_id}.srt")
        write_srt(arabic_segments, srt_path)

        step("burning_captions")
        output_path = str(OUTPUT_DIR / f"{job_id}_captioned.mp4")
        burn_subtitles(
            video_path,
            srt_path,
            output_path,
            font_size=font_size,
            segments=arabic_segments if use_emoji and source_language != "ar" else [],
        )

        # Cleanup temp files
        for f in [video_path, srt_path]:
            if os.path.exists(f):
                os.remove(f)

        jobs[job_id]["output"] = f"{job_id}_captioned.mp4"
        jobs[job_id]["status"] = "done"
        log.info("job %s: done output=%s", job_id, output_path)

        # Auto-delete output after 24 hours
        def _cleanup():
            time.sleep(86400)
            if os.path.exists(output_path):
                os.remove(output_path)

        threading.Thread(target=_cleanup, daemon=True).start()

    except Exception as e:
        log.exception("job %s: pipeline failed", job_id)
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        # Clean up on error
        for f in [video_path]:
            if os.path.exists(f):
                os.remove(f)


def _telegram_file_id(message: dict) -> str | None:
    if message.get("video"):
        return message["video"].get("file_id")

    document = message.get("document") or {}
    mime_type = document.get("mime_type") or ""
    if mime_type.startswith("video/"):
        return document.get("file_id")

    return None


def _telegram_text(message: dict) -> str:
    return (message.get("text") or message.get("caption") or "").strip()


def _looks_like_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def _run_telegram_job(
    chat_id: int | str,
    file_id: str | None = None,
    url: str = "",
    source_language: str = "en",
    font_size: int = 14,
):
    from services.downloader import download_video
    from services.telegram_bot import TelegramBot

    bot = TelegramBot(TELEGRAM_BOT_TOKEN)
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "starting", "steps": [], "error": None, "output": None}
    video_path = str(TEMP_DIR / f"{job_id}.mp4")

    try:
        _check_pipeline_keys(source_language)
        bot.send_message(chat_id, "Received. Processing captions now.")

        if file_id:
            jobs[job_id]["status"] = "downloading"
            bot.download_file(file_id, video_path)
        elif url:
            jobs[job_id]["status"] = "downloading"
            downloaded = download_video(url, video_path.replace(".mp4", ""))
            if os.path.exists(downloaded) and downloaded != video_path:
                shutil.move(downloaded, video_path)
        else:
            raise ValueError("Send a video file or a supported video URL.")

        _run_pipeline(
            job_id,
            video_path,
            font_size=font_size,
            use_emoji=False,
            source_language=source_language,
        )

        job = jobs[job_id]
        if job["status"] == "error":
            raise RuntimeError(job.get("error") or "Caption processing failed.")

        output_name = job.get("output")
        if not output_name:
            raise RuntimeError("Caption processing finished without an output file.")

        output_path = OUTPUT_DIR / output_name
        try:
            bot.send_video(chat_id, str(output_path), caption="Done.")
        except Exception:
            log.exception("telegram sendVideo failed for job %s; trying sendDocument", job_id)
            bot.send_document(chat_id, str(output_path), caption="Done.")

    except Exception as exc:
        log.exception("telegram job %s failed", job_id)
        try:
            bot.send_message(chat_id, f"Error: {exc}")
        except Exception:
            log.exception("failed to send Telegram error message for job %s", job_id)


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.post("/process")
async def process(
    url: str = Form(default=""),
    file: UploadFile = File(default=None),
    font_size: int = Form(default=22),
    use_emoji: bool = Form(default=False),
    source_language: str = Form(default="auto"),
):
    source_language = _valid_source_language(source_language)
    _check_pipeline_keys(source_language)

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "starting", "steps": [], "error": None, "output": None}

    video_path = str(TEMP_DIR / f"{job_id}.mp4")

    if file and file.filename:
        # Save uploaded file
        jobs[job_id]["status"] = "uploading"
        with open(video_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    elif url.strip():
        # Download from URL
        jobs[job_id]["status"] = "downloading"

        def _download():
            from services.downloader import download_video
            try:
                downloaded = download_video(url.strip(), video_path.replace(".mp4", ""))
                if os.path.exists(downloaded) and downloaded != video_path:
                    shutil.move(downloaded, video_path)
                _run_pipeline(job_id, video_path, font_size=font_size, use_emoji=use_emoji, source_language=source_language)
            except Exception as e:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)

        threading.Thread(target=_download, daemon=True).start()
        return {"job_id": job_id}
    else:
        raise HTTPException(status_code=400, detail="Provide a URL or upload a file.")

    threading.Thread(target=_run_pipeline, args=(job_id, video_path, font_size, use_emoji, source_language), daemon=True).start()
    return {"job_id": job_id}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN is not configured.")

    update = await request.json()
    message = update.get("message") or update.get("edited_message") or {}
    if not message:
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = _telegram_text(message)
    file_id = _telegram_file_id(message)

    if text in {"/start", "/help"}:
        from services.telegram_bot import TelegramBot

        TelegramBot(TELEGRAM_BOT_TOKEN).send_message(
            chat_id,
            "Send me a video file or a TikTok/YouTube/Instagram URL. "
            "I will translate English audio to Arabic captions with font size 14 and no emoji.",
        )
        return {"ok": True}

    if not file_id and not _looks_like_url(text):
        from services.telegram_bot import TelegramBot

        TelegramBot(TELEGRAM_BOT_TOKEN).send_message(
            chat_id,
            "Send a video file or a supported video URL.",
        )
        return {"ok": True}

    threading.Thread(
        target=_run_telegram_job,
        kwargs={
            "chat_id": chat_id,
            "file_id": file_id,
            "url": text if _looks_like_url(text) else "",
        },
        daemon=True,
    ).start()

    return {"ok": True}


@app.get("/progress/{job_id}")
async def progress(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def _stream():
        last_status = None
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {{'status': 'error', 'error': 'Job not found'}}\n\n"
                break

            current = job["status"]
            if current != last_status:
                last_status = current
                error = job.get("error") or ""
                output = job.get("output") or ""
                yield (
                    f"data: {{\"status\": \"{current}\", "
                    f"\"error\": \"{error}\", "
                    f"\"output\": \"{output}\"}}\n\n"
                )

            if current in ("done", "error"):
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/download/{filename}")
def download(filename: str):
    # Prevent path traversal
    safe = Path(filename).name
    path = OUTPUT_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found or expired.")
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=safe,
    )

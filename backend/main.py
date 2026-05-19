import asyncio
import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
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
TELEGRAM_DEFAULT_QUALITY = int(os.getenv("TELEGRAM_DEFAULT_QUALITY", "720"))
TELEGRAM_MAX_PART_SECONDS = max(60, int(os.getenv("TELEGRAM_MAX_PART_SECONDS", "900")))

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
telegram_quality_by_chat: dict[int | str, int] = {}


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
    video_crf: int = 18,
    video_preset: str = "fast",
    max_height: int | None = None,
    on_step: Callable[[str], None] | None = None,
):
    from services.transcriber import transcribe
    from services.translator import translate_to_arabic
    from services.srt_builder import write_srt
    from services.captioner import burn_subtitles
    from services.video_tools import normalize_landscape_to_vertical

    last_step_at = time.monotonic()
    cleanup_paths = {video_path}

    def step(msg: str):
        nonlocal last_step_at
        now = time.monotonic()
        if jobs[job_id]["status"] != "starting":
            log.info("job %s: step %s took %.1fs", job_id, jobs[job_id]["status"], now - last_step_at)
        last_step_at = now
        jobs[job_id]["steps"].append(msg)
        jobs[job_id]["status"] = msg
        if on_step:
            on_step(msg)

    try:
        log.info(
            "job %s: starting pipeline video=%s font_size=%s use_emoji=%s source_language=%s",
            job_id,
            video_path,
            font_size,
            use_emoji,
            source_language,
        )
        step("preparing_video")
        vertical_path = str(TEMP_DIR / f"{job_id}_vertical.mp4")
        prepared_video_path = normalize_landscape_to_vertical(video_path, vertical_path)
        if prepared_video_path != video_path:
            cleanup_paths.add(prepared_video_path)
            video_path = prepared_video_path

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
        cleanup_paths.add(srt_path)
        write_srt(arabic_segments, srt_path)

        step("burning_captions")
        output_path = str(OUTPUT_DIR / f"{job_id}_captioned.mp4")
        burn_subtitles(
            video_path,
            srt_path,
            output_path,
            font_size=font_size,
            segments=arabic_segments if use_emoji and source_language != "ar" else [],
            crf=video_crf,
            preset=video_preset,
            max_height=max_height,
        )

        # Cleanup temp files
        for f in cleanup_paths:
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
        for f in cleanup_paths:
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


def _telegram_quality(chat_id: int | str) -> int:
    return telegram_quality_by_chat.get(chat_id, TELEGRAM_DEFAULT_QUALITY)


def _set_telegram_quality(chat_id: int | str, text: str) -> str | None:
    parts = text.split()
    if len(parts) == 1:
        return f"Current quality: {_telegram_quality(chat_id)}p. Use /quality 720 or /quality 1080."

    if parts[1] not in {"720", "1080"}:
        return "Invalid quality. Use /quality 720 or /quality 1080."

    telegram_quality_by_chat[chat_id] = int(parts[1])
    return f"Quality set to {parts[1]}p."


def _run_telegram_job(
    chat_id: int | str,
    file_id: str | None = None,
    url: str = "",
    source_language: str = "en",
    font_size: int = 14,
    max_height: int = 720,
    status_message_id: int | None = None,
):
    from services.downloader import download_video
    from services.telegram_bot import TelegramBot
    from services.video_tools import split_video

    bot = TelegramBot(TELEGRAM_BOT_TOKEN)
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "starting", "steps": [], "error": None, "output": None}
    video_path = str(TEMP_DIR / f"{job_id}.mp4")
    split_part_paths: list[str] = []
    last_download_update_at = 0.0
    step_messages = {
        "preparing_video": "Preparing video format...",
        "extracting_audio": "Extracting audio...",
        "transcribed": "Transcription complete. Translating to Arabic...",
        "translating": "Translating to Arabic...",
        "building_srt": "Building subtitles...",
        "burning_captions": "Burning captions into the video...",
    }

    def set_status(text: str):
        nonlocal status_message_id
        if status_message_id is None:
            result = bot.send_message(chat_id, text)
            status_message_id = result.get("message_id")
            return

        try:
            bot.edit_message_text(chat_id, status_message_id, text)
        except Exception:
            log.exception("failed to edit Telegram status message for job %s", job_id)

    def download_progress(info: dict):
        nonlocal last_download_update_at
        now = time.monotonic()
        if now - last_download_update_at < 15 and info.get("status") != "finished":
            return

        last_download_update_at = now
        downloaded = info.get("downloaded_bytes") or 0
        total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
        if total:
            percent = min(100, int(downloaded * 100 / total))
            set_status(f"Downloading YouTube video at {max_height}p... {percent}%")
        else:
            set_status(f"Downloading YouTube video at {max_height}p...")

    try:
        _check_pipeline_keys(source_language)
        set_status("Received. Processing captions now.")

        if file_id:
            jobs[job_id]["status"] = "downloading"
            set_status("Downloading Telegram video...")
            bot.download_file(file_id, video_path)
        elif url:
            jobs[job_id]["status"] = "downloading"
            set_status(f"Downloading video from URL at {max_height}p...")
            downloaded = download_video(
                url,
                video_path.replace(".mp4", ""),
                max_height=max_height,
                progress_callback=download_progress,
            )
            if os.path.exists(downloaded) and downloaded != video_path:
                shutil.move(downloaded, video_path)
        else:
            raise ValueError("Send a video file or a supported video URL.")

        set_status("Checking video length...")
        parts = split_video(video_path, TEMP_DIR, job_id, TELEGRAM_MAX_PART_SECONDS)
        split_part_paths = parts if len(parts) > 1 else []
        if len(parts) > 1:
            set_status(f"Video is long. Split into {len(parts)} parts.")

        sent_count = 0
        for index, part_path in enumerate(parts, start=1):
            part_job_id = job_id if len(parts) == 1 else f"{job_id}_part_{index:03d}"
            if part_job_id != job_id:
                jobs[part_job_id] = {"status": "starting", "steps": [], "error": None, "output": None}

            part_label = f"Part {index}/{len(parts)}: " if len(parts) > 1 else ""

            def notify_part_step(status: str, label: str = part_label):
                message = step_messages.get(status)
                if message:
                    set_status(label + message)

            set_status(part_label + "Processing captions now.")
            _run_pipeline(
                part_job_id,
                part_path,
                font_size=font_size,
                use_emoji=False,
                source_language=source_language,
                max_height=max_height,
                on_step=notify_part_step,
            )

            job = jobs[part_job_id]
            if job["status"] == "error":
                raise RuntimeError(job.get("error") or "Caption processing failed.")

            output_name = job.get("output")
            if not output_name:
                raise RuntimeError("Caption processing finished without an output file.")

            output_path = OUTPUT_DIR / output_name
            caption = f"Done. Part {index}/{len(parts)}." if len(parts) > 1 else "Done."
            set_status(part_label + f"Uploading {max_height}p MP4 to Telegram...")
            try:
                bot.send_document(chat_id, str(output_path), caption=caption)
            except Exception:
                log.exception("telegram sendDocument failed for job %s; trying sendVideo", part_job_id)
                bot.send_video(chat_id, str(output_path), caption=caption)
            sent_count += 1

        if len(parts) > 1 and os.path.exists(video_path):
            os.remove(video_path)

        set_status(f"Done. Sent {sent_count} file(s).")

    except Exception as exc:
        log.exception("telegram job %s failed", job_id)
        for path in [video_path, *split_part_paths]:
            if os.path.exists(path):
                os.remove(path)
        try:
            set_status(f"Error: {exc}")
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
                log.exception("job %s: URL pipeline failed", job_id)
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
            "I will translate English audio to Arabic captions with font size 14 and no emoji. "
            "Use /quality 720 or /quality 1080. Default is 720p.",
        )
        return {"ok": True}

    if text.startswith("/quality"):
        from services.telegram_bot import TelegramBot

        TelegramBot(TELEGRAM_BOT_TOKEN).send_message(chat_id, _set_telegram_quality(chat_id, text))
        return {"ok": True}

    if not file_id and not _looks_like_url(text):
        from services.telegram_bot import TelegramBot

        TelegramBot(TELEGRAM_BOT_TOKEN).send_message(
            chat_id,
            "Send a video file or a supported video URL.",
        )
        return {"ok": True}

    from services.telegram_bot import TelegramBot

    status_message = TelegramBot(TELEGRAM_BOT_TOKEN).send_message(
        chat_id,
        f"Queued. I will download at {_telegram_quality(chat_id)}p and split long videos automatically.",
    )
    status_message_id = status_message.get("message_id")

    threading.Thread(
        target=_run_telegram_job,
        kwargs={
            "chat_id": chat_id,
            "file_id": file_id,
            "url": text if _looks_like_url(text) else "",
            "max_height": _telegram_quality(chat_id),
            "status_message_id": status_message_id,
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
        last_emit_at = 0.0
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'status': 'error', 'error': 'Job not found'})}\n\n"
                break

            current = job["status"]
            now = time.monotonic()
            should_emit = current != last_status or now - last_emit_at >= 10
            if should_emit:
                last_status = current
                last_emit_at = now
                payload = {
                    "status": current,
                    "error": job.get("error") or "",
                    "output": job.get("output") or "",
                }
                yield f"data: {json.dumps(payload)}\n\n"

            if current in ("done", "error"):
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/status/{job_id}")
def status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return {
        "status": job["status"],
        "error": job.get("error") or "",
        "output": job.get("output") or "",
    }


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

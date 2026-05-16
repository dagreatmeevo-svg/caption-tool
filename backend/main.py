import asyncio
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

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


def _run_pipeline(job_id: str, video_path: str, font_size: int = 22, use_emoji: bool = False):
    from services.downloader import download_video
    from services.transcriber import transcribe
    from services.translator import translate_to_arabic
    from services.srt_builder import write_srt
    from services.captioner import burn_subtitles

    def step(msg: str):
        jobs[job_id]["steps"].append(msg)
        jobs[job_id]["status"] = msg

    try:
        step("extracting_audio")
        segments = transcribe(video_path, GROQ_API_KEY)

        step("transcribed")
        if not segments:
            raise ValueError("No speech detected in video.")

        step("translating")
        arabic_segments = translate_to_arabic(segments, DEEPSEEK_API_KEY, use_emoji=use_emoji)

        step("building_srt")
        srt_path = str(TEMP_DIR / f"{job_id}.srt")
        write_srt(arabic_segments, srt_path)

        step("burning_captions")
        output_path = str(OUTPUT_DIR / f"{job_id}_captioned.mp4")
        burn_subtitles(video_path, srt_path, output_path, font_size=font_size)

        # Cleanup temp files
        for f in [video_path, srt_path]:
            if os.path.exists(f):
                os.remove(f)

        jobs[job_id]["output"] = f"{job_id}_captioned.mp4"
        jobs[job_id]["status"] = "done"

        # Auto-delete output after 24 hours
        def _cleanup():
            time.sleep(86400)
            if os.path.exists(output_path):
                os.remove(output_path)

        threading.Thread(target=_cleanup, daemon=True).start()

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        # Clean up on error
        for f in [video_path]:
            if os.path.exists(f):
                os.remove(f)


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.post("/process")
async def process(
    url: str = Form(default=""),
    file: UploadFile = File(default=None),
    font_size: int = Form(default=22),
    use_emoji: bool = Form(default=False),
):
    if not GROQ_API_KEY or not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="API keys not configured in .env")

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
                _run_pipeline(job_id, video_path, font_size=font_size, use_emoji=use_emoji)
            except Exception as e:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)

        threading.Thread(target=_download, daemon=True).start()
        return {"job_id": job_id}
    else:
        raise HTTPException(status_code=400, detail="Provide a URL or upload a file.")

    threading.Thread(target=_run_pipeline, args=(job_id, video_path, font_size, use_emoji), daemon=True).start()
    return {"job_id": job_id}


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

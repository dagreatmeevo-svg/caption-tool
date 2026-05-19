import base64
import os
from pathlib import Path

import yt_dlp

_COOKIE_ENV = "YTDLP_COOKIES"
_COOKIE_B64_ENV = "YTDLP_COOKIES_B64"
_COOKIE_B64_PART_PREFIX = "YTDLP_COOKIES_B64_"
_COOKIE_FILE_ENV = "YTDLP_COOKIES_FILE"


def _joined_env_parts(prefix: str) -> str:
    values = []
    for index in range(1, 51):
        value = os.getenv(f"{prefix}{index}")
        if not value:
            break
        values.append(value.strip())
    return "".join(values)


def _cookies_file() -> str | None:
    configured = os.getenv(_COOKIE_FILE_ENV)
    if configured and os.path.exists(configured):
        return configured

    cookies = os.getenv(_COOKIE_ENV)
    encoded_cookies = os.getenv(_COOKIE_B64_ENV) or _joined_env_parts(_COOKIE_B64_PART_PREFIX)
    if not cookies and encoded_cookies:
        cookies = base64.b64decode(encoded_cookies).decode("utf-8")
    if not cookies:
        return None

    cookies = cookies.replace("\\n", "\n")
    if not cookies.endswith("\n"):
        cookies += "\n"

    path = Path(__file__).resolve().parent.parent / "temp" / "youtube_cookies.txt"
    path.parent.mkdir(exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8", errors="ignore") != cookies:
        path.write_text(cookies, encoding="utf-8")
    return str(path)


def _format_selector(max_height: int | None) -> str:
    if not max_height:
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    return (
        f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={max_height}]+bestaudio/"
        f"best[height<={max_height}][ext=mp4]/"
        f"best[height<={max_height}]/best"
    )


def download_video(url: str, output_path: str, max_height: int | None = None, progress_callback=None) -> str:
    """Download video from URL using yt-dlp. Returns the final file path."""
    ydl_opts = {
        "format": _format_selector(max_height),
        "merge_output_format": "mp4",
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if progress_callback:
        ydl_opts["progress_hooks"] = [progress_callback]

    cookies_file = _cookies_file()
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # yt-dlp may append extension, find the actual file
        filename = ydl.prepare_filename(info)
        # Normalize extension — yt-dlp may write .mp4 directly
        if not os.path.exists(filename):
            filename = os.path.splitext(filename)[0] + ".mp4"
        return filename

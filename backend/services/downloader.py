import base64
import logging
import os
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError

_COOKIE_ENV = "YTDLP_COOKIES"
_COOKIE_B64_ENV = "YTDLP_COOKIES_B64"
_COOKIE_B64_PART_PREFIX = "YTDLP_COOKIES_B64_"
_COOKIE_FILE_ENV = "YTDLP_COOKIES_FILE"
log = logging.getLogger(__name__)


def _joined_env_parts(prefix: str) -> tuple[str, int]:
    values = []
    for index in range(1, 51):
        value = os.getenv(f"{prefix}{index}")
        if not value:
            break
        values.append(value.strip())
    return "".join(values), len(values)


def _cookies_file() -> str | None:
    configured = os.getenv(_COOKIE_FILE_ENV)
    if configured and os.path.exists(configured):
        return configured

    cookies = os.getenv(_COOKIE_ENV)
    chunked_cookies, chunk_count = _joined_env_parts(_COOKIE_B64_PART_PREFIX)
    encoded_cookies = chunked_cookies or os.getenv(_COOKIE_B64_ENV)
    if not cookies and encoded_cookies:
        try:
            cookies = base64.b64decode(encoded_cookies).decode("utf-8")
        except Exception as exc:
            raise RuntimeError(
                "YouTube cookies are configured but could not be decoded. "
                "Recreate the YTDLP_COOKIES_B64 variables from the cookies file."
            ) from exc
    if not cookies:
        log.warning("yt-dlp cookies are not configured")
        return None

    cookies = cookies.replace("\\n", "\n")
    if not cookies.endswith("\n"):
        cookies += "\n"

    if "# Netscape HTTP Cookie File" not in cookies[:500]:
        log.warning("yt-dlp cookies do not look like a Netscape cookies export")

    cookie_rows = [line for line in cookies.splitlines() if line and not line.startswith("#")]
    path = Path(__file__).resolve().parent.parent / "temp" / "youtube_cookies.txt"
    path.parent.mkdir(exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8", errors="ignore") != cookies:
        path.write_text(cookies, encoding="utf-8")
    log.info("yt-dlp cookies loaded: chunks=%s rows=%s path=%s", chunk_count, len(cookie_rows), path)
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

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # yt-dlp may append extension, find the actual file
            filename = ydl.prepare_filename(info)
            # Normalize extension — yt-dlp may write .mp4 directly
            if not os.path.exists(filename):
                filename = os.path.splitext(filename)[0] + ".mp4"
            return filename
    except DownloadError as exc:
        message = str(exc)
        if "Sign in to confirm" in message or "not a bot" in message:
            raise RuntimeError(
                "YouTube rejected the download as not signed in. "
                "Check Railway variables YTDLP_COOKIES_B64_1..N are all present, "
                "remove any old YTDLP_COOKIES_B64 variable, then redeploy. "
                "If it still fails, export fresh YouTube cookies from the same browser profile."
            ) from exc
        raise

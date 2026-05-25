import base64
import hashlib
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp
from yt_dlp.utils import DownloadError

_COOKIE_ENV = "YTDLP_COOKIES"
_COOKIE_B64_ENV = "YTDLP_COOKIES_B64"
_COOKIE_B64_PART_PREFIX = "YTDLP_COOKIES_B64_"
_COOKIE_FILE_ENV = "YTDLP_COOKIES_FILE"
_INSTAGRAM_COOKIE_ENV = "INSTAGRAM_COOKIES"
_INSTAGRAM_COOKIE_B64_ENV = "INSTAGRAM_COOKIES_B64"
_INSTAGRAM_COOKIE_B64_PART_PREFIX = "INSTAGRAM_COOKIES_B64_"
_INSTAGRAM_COOKIE_FILE_ENV = "INSTAGRAM_COOKIES_FILE"
log = logging.getLogger(__name__)

_CDREADER_API_BASE = "https://videoapi-hk.cdreader.com/video"
_CDREADER_SIGN_CHARS = "abcdefhijkmnprstwxyz0123456789"


def _joined_env_parts(prefix: str) -> tuple[str, int]:
    values = []
    for index in range(1, 51):
        value = os.getenv(f"{prefix}{index}")
        if not value:
            break
        values.append(value.strip())
    return "".join(values), len(values)


def _is_instagram_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname == "instagram.com" or hostname.endswith(".instagram.com")


def _has_cookie_config(cookie_env: str, b64_env: str, part_prefix: str, file_env: str) -> bool:
    return bool(
        os.getenv(cookie_env)
        or os.getenv(b64_env)
        or os.getenv(f"{part_prefix}1")
        or os.getenv(file_env)
    )


def _cookies_file(url: str) -> str | None:
    label = "yt-dlp"
    cookie_env = _COOKIE_ENV
    b64_env = _COOKIE_B64_ENV
    part_prefix = _COOKIE_B64_PART_PREFIX
    file_env = _COOKIE_FILE_ENV
    filename = "youtube_cookies.txt"

    if _is_instagram_url(url) and _has_cookie_config(
        _INSTAGRAM_COOKIE_ENV,
        _INSTAGRAM_COOKIE_B64_ENV,
        _INSTAGRAM_COOKIE_B64_PART_PREFIX,
        _INSTAGRAM_COOKIE_FILE_ENV,
    ):
        label = "Instagram"
        cookie_env = _INSTAGRAM_COOKIE_ENV
        b64_env = _INSTAGRAM_COOKIE_B64_ENV
        part_prefix = _INSTAGRAM_COOKIE_B64_PART_PREFIX
        file_env = _INSTAGRAM_COOKIE_FILE_ENV
        filename = "instagram_cookies.txt"

    configured = os.getenv(file_env)
    if configured and os.path.exists(configured):
        log.info("%s cookies loaded from configured file", label)
        return configured

    cookies = os.getenv(cookie_env)
    chunked_cookies, chunk_count = _joined_env_parts(part_prefix)
    encoded_cookies = chunked_cookies or os.getenv(b64_env)
    if not cookies and encoded_cookies:
        try:
            cookies = base64.b64decode(encoded_cookies).decode("utf-8")
        except Exception as exc:
            raise RuntimeError(
                f"{label} cookies are configured but could not be decoded. "
                f"Recreate the {b64_env} variables from the cookies file."
            ) from exc
    if not cookies:
        log.warning("yt-dlp cookies are not configured")
        return None

    cookies = cookies.replace("\\n", "\n")
    if not cookies.endswith("\n"):
        cookies += "\n"

    if "# Netscape HTTP Cookie File" not in cookies[:500]:
        log.warning("%s cookies do not look like a Netscape cookies export", label)

    cookie_rows = [line for line in cookies.splitlines() if line and not line.startswith("#")]
    path = Path(__file__).resolve().parent.parent / "temp" / filename
    path.parent.mkdir(exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8", errors="ignore") != cookies:
        path.write_text(cookies, encoding="utf-8")
    log.info("%s cookies loaded: chunks=%s rows=%s path=%s", label, chunk_count, len(cookie_rows), path)
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


def _is_cdreader_share_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in {"p.cdreader.com", "www.cdreader.com", "cdreader.com"} and (
        parsed.path.endswith("/videoShare.html") or "videoShare.html" in parsed.path
    )


def _is_goodshort_share_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    return hostname in {"goodshort.com", "www.goodshort.com"} and parsed.path.startswith("/share/")


def _cdreader_nonce(length: int = 32) -> str:
    return "".join(random.choice(_CDREADER_SIGN_CHARS) for _ in range(length))


def _cdreader_clientinfo(langid: str, corever: str) -> str:
    keys = [
        "device",
        "device2",
        "device3",
        "sw",
        "sh",
        "osver",
        "corever",
        "appver",
        "mt",
        "appid",
        "langid",
        "chl",
        "androidid",
        "utcoffset",
        "supportutctime",
        "timestamp",
        "ver",
        "userid",
        "build",
        "UniqueCdReaderId",
        "syslanguage",
        "locale",
        "idfa",
        "sendid",
    ]
    data = {key: "" for key in keys}
    data["corever"] = corever or "1"
    data["langid"] = langid or "10"
    return json.dumps(data, separators=(",", ":"))


def _extract_cdreader_media_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    series_id = (query.get("seriesId") or query.get("seriesid") or [""])[0]
    epis_id = (query.get("episId") or query.get("episid") or [""])[0]
    langid = (query.get("langid") or ["10"])[0]
    corever = (query.get("corever") or ["1"])[0]

    if not series_id:
        raise RuntimeError("MoboReels link is missing seriesId.")

    payload = {"seriesId": series_id, "episId": epis_id}
    nonce = _cdreader_nonce()
    timestamp = str(int(time.time() * 1000))
    sign_parts = f"nonce={nonce}&ts={timestamp}"
    for key in sorted(payload):
        if payload[key]:
            sign_parts += f"&{key}={payload[key]}"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://p.cdreader.com",
        "Referer": url,
        "nonce": nonce,
        "ts": timestamp,
        "x-sign": hashlib.md5(sign_parts.encode("utf-8")).hexdigest(),
        "clientinfo": _cdreader_clientinfo(langid, corever),
    }

    response = requests.post(
        f"{_CDREADER_API_BASE}/h5/series/seriesDetail",
        json=payload,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200:
        raise RuntimeError(data.get("message") or "MoboReels did not return a downloadable video.")

    result = data.get("data") or {}
    episode = result.get("episVO") or {}
    media_url = episode.get("mediaUrl") or episode.get("m3u8Url")
    if not media_url:
        raise RuntimeError("MoboReels share page did not expose a downloadable episode.")

    return media_url


def _extract_goodshort_media_url(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    match = re.search(r"window\.__INITIAL_STATE__=(.*?);\(function\(\)", response.text, re.S)
    if not match:
        raise RuntimeError("GoodShort share page did not include public video data.")

    state = json.loads(match.group(1))
    first_chapter = (state.get("shareModule") or {}).get("firstChapter") or {}
    media_url = first_chapter.get("m3u8Path")
    if not media_url:
        raise RuntimeError("GoodShort share page did not expose a downloadable episode.")

    return media_url


def _download_direct_url(url: str, output_path: str, progress_callback=None, headers: dict | None = None) -> str:
    final_path = output_path if Path(output_path).suffix else f"{output_path}.mp4"
    Path(final_path).parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, headers=headers or {}, stream=True, timeout=300) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        downloaded = 0
        with open(final_path, "wb") as out:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                out.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback({
                        "status": "downloading",
                        "downloaded_bytes": downloaded,
                        "total_bytes": total,
                    })

    if progress_callback:
        progress_callback({
            "status": "finished",
            "downloaded_bytes": os.path.getsize(final_path),
            "total_bytes": os.path.getsize(final_path),
        })
    return final_path


def download_video(url: str, output_path: str, max_height: int | None = None, progress_callback=None) -> str:
    """Download video from URL using yt-dlp. Returns the final file path."""
    http_headers = None
    if _is_cdreader_share_url(url):
        media_url = _extract_cdreader_media_url(url)
        log.info("resolved MoboReels/CDReader share URL to media URL")
        return _download_direct_url(
            media_url,
            output_path,
            progress_callback=progress_callback,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": url,
                "Origin": "https://p.cdreader.com",
            },
        )
    if _is_goodshort_share_url(url):
        url = _extract_goodshort_media_url(url)
        http_headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.goodshort.com/",
        }
        log.info("resolved GoodShort share URL to HLS media URL")

    ydl_opts = {
        "format": _format_selector(max_height),
        "merge_output_format": "mp4",
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
    }
    if http_headers:
        ydl_opts["http_headers"] = http_headers
    if progress_callback:
        ydl_opts["progress_hooks"] = [progress_callback]

    cookies_file = _cookies_file(url)
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
        message_lower = message.lower()
        if _is_instagram_url(url) and any(
            marker in message_lower
            for marker in ("requested content is not available", "rate-limit", "login required")
        ):
            raise RuntimeError(
                "Instagram rejected the download because login cookies are missing, expired, "
                "or the post is unavailable to that account. Export fresh Instagram cookies in "
                "Netscape format, set Railway variables INSTAGRAM_COOKIES_B64_1..N "
                "(or INSTAGRAM_COOKIES_B64), then redeploy."
            ) from exc
        if "Sign in to confirm" in message or "not a bot" in message:
            raise RuntimeError(
                "YouTube rejected the download as not signed in. "
                "Check Railway variables YTDLP_COOKIES_B64_1..N are all present, "
                "remove any old YTDLP_COOKIES_B64 variable, then redeploy. "
                "If it still fails, export fresh YouTube cookies from the same browser profile."
            ) from exc
        raise

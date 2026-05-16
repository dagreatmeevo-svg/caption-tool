import yt_dlp
import os


def download_video(url: str, output_path: str) -> str:
    """Download video from URL using yt-dlp. Returns the final file path."""
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # yt-dlp may append extension, find the actual file
        filename = ydl.prepare_filename(info)
        # Normalize extension — yt-dlp may write .mp4 directly
        if not os.path.exists(filename):
            filename = os.path.splitext(filename)[0] + ".mp4"
        return filename

import json
import logging
import math
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _run(cmd: list[str]):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{cmd[0]} exited with {result.returncode}")
    return result.stdout


def probe_video(path: str) -> dict:
    raw = _run([
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
    ])
    data = json.loads(raw)
    stream = data["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(data.get("format", {}).get("duration") or 0),
    }


def duration_seconds(path: str) -> float:
    return probe_video(path)["duration"]


def normalize_landscape_to_vertical(path: str, output_path: str) -> str:
    info = probe_video(path)
    width = info["width"]
    height = info["height"]
    if width <= height:
        return path

    log.info("converting landscape video to 9:16: %sx%s path=%s", width, height, path)
    filter_graph = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=24[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
    )
    _run([
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-i", path,
        "-filter_complex", filter_graph,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-threads", os.getenv("CAPTION_FFMPEG_THREADS", "1"),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ])
    return output_path


def split_video(path: str, output_dir: str | Path, base_name: str, max_seconds: int) -> list[str]:
    duration = duration_seconds(path)
    if duration <= max_seconds:
        return [path]

    parts = max(1, math.ceil(duration / max_seconds))
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    results = []

    for idx in range(parts):
        start = idx * max_seconds
        part_path = output_dir / f"{base_name}_part_{idx + 1:03d}.mp4"
        _run([
            "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
            "-ss", str(start),
            "-i", path,
            "-t", str(max_seconds),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(part_path),
        ])
        results.append(str(part_path))

    log.info("split video path=%s duration=%.1fs into %s parts", path, duration, len(results))
    return results


def split_video_by_size(path: str, output_dir: str | Path, base_name: str, max_bytes: int) -> list[str]:
    size = os.path.getsize(path)
    if size <= max_bytes:
        return [path]

    duration = duration_seconds(path)
    if duration <= 0:
        return [path]

    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    # Re-encode upload chunks. Stream-copy cuts can be much larger than expected
    # when keyframes are sparse, which is exactly what causes Telegram 413s.
    estimated_parts = max(2, math.ceil(size / (max_bytes * 0.75)))
    max_seconds = max(1, int(duration / estimated_parts))

    for attempt in range(3):
        results = []
        parts = max(1, math.ceil(duration / max_seconds))
        for idx in range(parts):
            start = idx * max_seconds
            part_path = output_dir / f"{base_name}_upload_{attempt + 1}_{idx + 1:03d}.mp4"
            _run([
                "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                "-ss", str(start),
                "-i", path,
                "-t", str(max_seconds),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-threads", os.getenv("CAPTION_FFMPEG_THREADS", "1"),
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                str(part_path),
            ])
            results.append(str(part_path))

        if all(os.path.getsize(part) <= max_bytes for part in results):
            log.info("split oversized video path=%s size=%s into %s upload parts", path, size, len(results))
            return results

        if attempt == 2:
            log.warning("upload split still has oversized parts after retries path=%s", path)
            return results

        for part in results:
            if os.path.exists(part):
                os.remove(part)
        max_seconds = max(1, int(max_seconds * 0.6))

    return [path]

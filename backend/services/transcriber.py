import os
import math
import subprocess
import tempfile
from groq import Groq

GROQ_MAX_BYTES = 24 * 1024 * 1024  # 24MB safety margin under 25MB limit


def _extract_audio(video_path: str, audio_path: str):
    """Extract mono 16kHz MP3 from video — optimal for Whisper."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "mp3",
            audio_path,
        ],
        check=True,
        capture_output=True,
    )


def _transcribe_file(client: Groq, path: str, offset_seconds: float = 0.0) -> list[dict]:
    """Send one audio file to Groq Whisper and return segments with offset applied."""
    with open(path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=f,
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = []
    for seg in result.segments:
        if isinstance(seg, dict):
            start, end, text = seg["start"], seg["end"], seg["text"]
        else:
            start, end, text = seg.start, seg.end, seg.text
        segments.append({
            "start": start + offset_seconds,
            "end": end + offset_seconds,
            "text": text.strip(),
        })
    return segments


def _split_audio(audio_path: str, chunk_dir: str) -> list[tuple[str, float]]:
    """
    Split audio into chunks under GROQ_MAX_BYTES.
    Returns list of (chunk_path, start_offset_seconds).
    """
    size = os.path.getsize(audio_path)
    if size <= GROQ_MAX_BYTES:
        return [(audio_path, 0.0)]

    # Get duration via ffprobe
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, check=True,
    )
    duration = float(probe.stdout.strip())

    # How many chunks needed
    n_chunks = math.ceil(size / GROQ_MAX_BYTES)
    chunk_duration = duration / n_chunks

    chunks = []
    for i in range(n_chunks):
        start = i * chunk_duration
        chunk_path = os.path.join(chunk_dir, f"chunk_{i:03d}.mp3")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", str(start), "-t", str(chunk_duration),
                "-c", "copy", chunk_path,
            ],
            check=True, capture_output=True,
        )
        chunks.append((chunk_path, start))

    return chunks


def transcribe(video_path: str, api_key: str) -> list[dict]:
    """
    Full pipeline: extract audio → split if needed → transcribe via Groq.
    Returns list of {start, end, text} segments.
    """
    client = Groq(api_key=api_key)

    audio_path = video_path.replace(".mp4", "_audio.mp3")
    _extract_audio(video_path, audio_path)

    with tempfile.TemporaryDirectory() as chunk_dir:
        chunks = _split_audio(audio_path, chunk_dir)
        all_segments = []
        for chunk_path, offset in chunks:
            segs = _transcribe_file(client, chunk_path, offset_seconds=offset)
            all_segments.extend(segs)

    # Clean up audio file
    if os.path.exists(audio_path):
        os.remove(audio_path)

    return all_segments

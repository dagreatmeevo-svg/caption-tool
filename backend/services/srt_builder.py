def _fmt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments: list[dict]) -> str:
    """Convert list of {start, end, text} to SRT format string."""
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _fmt_time(seg["start"])
        end = _fmt_time(seg["end"])
        text = seg["text"].strip()
        if not text:
            continue
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def write_srt(segments: list[dict], path: str):
    content = build_srt(segments)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

import re
import logging

log = logging.getLogger(__name__)


def _safe_for_cairo(text: str) -> str:
    """
    Keep only codepoints that Cairo-Regular.ttf can render.
    Everything else (emoji, symbols, variation selectors, ZWJ…) is dropped.
    Allowlist is more reliable than a blocklist because Unicode adds new
    emoji ranges with every release.
    """
    out = []
    for ch in text:
        cp = ord(ch)
        if (
            0x0020 <= cp <= 0x007E    # Basic ASCII (space → ~)
            or 0x00A0 <= cp <= 0x024F  # Latin-1 + Latin Extended-A/B
            or 0x0600 <= cp <= 0x06FF  # Arabic
            or 0x0750 <= cp <= 0x077F  # Arabic Supplement
            or 0xFB50 <= cp <= 0xFDFF  # Arabic Presentation Forms-A
            or 0xFE70 <= cp <= 0xFEFF  # Arabic Presentation Forms-B
        ):
            out.append(ch)
    cleaned = ''.join(out).strip()
    return cleaned


def _fmt_time(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments: list[dict]) -> str:
    lines = []
    srt_idx = 1
    for seg in segments:
        text = _safe_for_cairo(seg["text"])
        if not text:
            continue
        start = _fmt_time(seg["start"])
        end   = _fmt_time(seg["end"])
        lines.append(f"{srt_idx}\n{start} --> {end}\n{text}\n")
        srt_idx += 1
    content = "\n".join(lines)
    log.debug("SRT content:\n%s", content[:500])
    return content


def write_srt(segments: list[dict], path: str):
    content = build_srt(segments)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

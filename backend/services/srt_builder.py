import logging
import re
import unicodedata

log = logging.getLogger(__name__)

_PUNCT_TRANSLATION = str.maketrans({
    "\u060c": ",",
    "\u061b": ";",
    "\u061f": "?",
})

_RTL_START = "\u202b"
_RTL_END = "\u202c"


def _safe_for_cairo(text: str) -> str:
    """Return subtitle text that Cairo + libass can render predictably."""
    out = []
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT_TRANSLATION)
    for ch in text:
        cp = ord(ch)
        category = unicodedata.category(ch)
        if category.startswith("M") or category in {"Cf", "Cc"}:
            continue
        if (
            0x0020 <= cp <= 0x007E
            or 0x00A0 <= cp <= 0x024F
            or (0x0600 <= cp <= 0x06FF and category[0] in {"L", "N"})
            or (0x0750 <= cp <= 0x077F and category[0] in {"L", "N"})
            or (0xFB50 <= cp <= 0xFDFF and category[0] in {"L", "N"})
            or (0xFE70 <= cp <= 0xFEFF and category[0] in {"L", "N"})
        ):
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _has_arabic(text: str) -> bool:
    return any(
        0x0600 <= ord(ch) <= 0x06FF
        or 0x0750 <= ord(ch) <= 0x077F
        or 0xFB50 <= ord(ch) <= 0xFDFF
        or 0xFE70 <= ord(ch) <= 0xFEFF
        for ch in text
    )


def _force_rtl(text: str) -> str:
    if not _has_arabic(text):
        return text
    return f"{_RTL_START}{text}{_RTL_END}"


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _codepoint_dump(text: str) -> str:
    rows = []
    for idx, ch in enumerate(text):
        if ord(ch) <= 0x7F or ch in "\r\n":
            continue
        rows.append(f"{idx}: U+{ord(ch):04X} {ch!r}")
    return "\n".join(rows) if rows else "(no non-ASCII codepoints)"


def build_srt(segments: list[dict]) -> str:
    lines = []
    srt_idx = 1
    for seg in segments:
        text = _safe_for_cairo(seg["text"])
        if not text:
            continue
        start = _fmt_time(seg["start"])
        end = _fmt_time(seg["end"])
        lines.append(f"{srt_idx}\n{start} --> {end}\n{_force_rtl(text)}\n")
        srt_idx += 1
    content = "\n".join(lines)
    log.info("SRT content before ffmpeg:\n%s", content)
    log.info("SRT non-ASCII codepoints:\n%s", _codepoint_dump(content))
    return content


def write_srt(segments: list[dict], path: str):
    content = build_srt(segments)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

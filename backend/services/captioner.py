import os
import logging
import re
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path

_DIR      = os.path.dirname(__file__)
_FONT_DIR = os.path.join(_DIR, "..", "fonts")
_FONT_FILE = os.path.join(_FONT_DIR, "Cairo-Regular.ttf")
_WINDOWS_FONT_DIR = r"C:\Windows\Fonts"
_LINUX_NOTO_NASKH = "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"
_EMOJI_CACHE = os.path.join(_DIR, "..", "emoji_cache")
log = logging.getLogger(__name__)

# Same comprehensive regex as srt_builder — must stay in sync
_EMOJI_RE = re.compile(
    '['
    '\U0001F300-\U0001F9FF'
    '\U0001FA00-\U0001FAFF'
    '\U0001F1E0-\U0001F1FF'
    '\U00002600-\U000027BF'
    '\U00002B00-\U00002BFF'
    '\U00002300-\U000023FF'
    '\U00002500-\U000025FF'
    '\U0000FE00-\U0000FE0F'
    '\U0001F004'
    '\U0001F0CF'
    '\U0000200D'
    '\U000020E3'
    ']+'
)

# Match individual emoji chars (single codepoints for Twemoji lookup)
_SINGLE_EMOJI_RE = re.compile(
    '['
    '\U0001F300-\U0001F9FF'
    '\U0001FA00-\U0001FAFF'
    '\U0001F1E0-\U0001F1FF'
    '\U00002600-\U000027BF'
    '\U00002B00-\U00002BFF'
    '\U0001F004'
    '\U0001F0CF'
    ']'
)


def _find_emojis(text: str) -> list[str]:
    return _SINGLE_EMOJI_RE.findall(text)


def _twemoji_png(char: str) -> str | None:
    """Download Twemoji 72x72 PNG, return local path or None on failure."""
    os.makedirs(_EMOJI_CACHE, exist_ok=True)
    cp   = f'{ord(char):x}'
    dest = os.path.join(_EMOJI_CACHE, f'{cp}.png')
    if not os.path.exists(dest):
        url = f'https://cdn.jsdelivr.net/npm/twemoji@14.0.2/assets/72x72/{cp}.png'
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception:
            return None
    return dest if os.path.exists(dest) else None


def _ffmpeg_filter_path(path: str) -> str:
    return os.path.normpath(path).replace('\\', '/').replace(':', '\\:')


def _codepoint_dump(text: str) -> str:
    rows = []
    for idx, ch in enumerate(text):
        if ord(ch) <= 0x7F or ch in "\r\n":
            continue
        rows.append(f"{idx}: U+{ord(ch):04X} {ch!r}")
    return "\n".join(rows) if rows else "(no non-ASCII codepoints)"


def _caption_font() -> tuple[str, str | None]:
    configured = os.getenv("CAPTION_FONT_NAME", "").strip()
    if configured:
        configured_dir = os.getenv("CAPTION_FONT_DIR", "").strip()
        return configured, configured_dir or None

    if os.name == "nt" and os.path.exists(os.path.join(_WINDOWS_FONT_DIR, "tahoma.ttf")):
        return "Tahoma", None

    if os.path.exists(_LINUX_NOTO_NASKH):
        return "Noto Naskh Arabic", None

    return "Cairo", _FONT_DIR


def _subtitles_filter(srt_esc: str, fonts_dir: str | None, style: str) -> str:
    parts = [f"subtitles='{srt_esc}'"]
    if fonts_dir:
        parts.append(f"fontsdir='{_ffmpeg_filter_path(fonts_dir)}'")
    parts.append(f"force_style='{style}'")
    return ":".join(parts)


def burn_subtitles(
    video_path: str,
    srt_path:   str,
    output_path: str,
    font_size:  int = 22,
    segments:   list[dict] | None = None,   # original segments (with emojis) for overlay
):
    srt_esc = _ffmpeg_filter_path(srt_path)
    fontsdir_mode = os.getenv("CAPTION_FONTSDIR_MODE", "dir").lower()
    font_name, font_dir = _caption_font()
    if font_name == "Cairo":
        font_dir = _FONT_FILE if fontsdir_mode == "file" else _FONT_DIR
    fonts_source = font_dir or "(system font provider)"
    fonts_dir = _ffmpeg_filter_path(font_dir) if font_dir else None

    srt_content = Path(srt_path).read_text(encoding='utf-8')
    log.info("captioner SRT path=%s bytes=%s", srt_path, Path(srt_path).stat().st_size)
    log.info("captioner SRT content right before ffmpeg:\n%s", srt_content)
    log.info("captioner SRT non-ASCII codepoints:\n%s", _codepoint_dump(srt_content))
    log.info(
        "captioner font config name=%s mode=%s source=%s exists=%s escaped=%s",
        font_name,
        fontsdir_mode,
        fonts_source,
        os.path.exists(font_dir) if font_dir else True,
        fonts_dir,
    )

    style = (
        f'FontName={font_name},'
        f'FontSize={font_size},'
        f'Alignment=2,'
        f'MarginV=35,'
        f'PrimaryColour=&H00FFFFFF,'
        f'OutlineColour=&H00000000,'
        f'BorderStyle=1,'
        f'Outline=2,'
        f'Shadow=0'
    )
    subtitle_filter = _subtitles_filter(srt_esc, font_dir, style)

    # Build emoji overlay map from original segments (SRT is already clean)
    # Group by (img_path, slot) → list of (start, end) intervals
    slot_intervals: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    if segments:
        for seg in segments:
            for slot, ch in enumerate(_find_emojis(seg.get('text', ''))):
                img = _twemoji_png(ch)
                if img:
                    slot_intervals[(img, slot)].append(
                        (seg['start'], seg['end'])
                    )

    em_px  = max(28, min(80, int(font_size * 2.0)))
    em_gap = 8
    em_y   = f'H-35-{int(font_size * 1.9)}-{em_px}-8'

    filter_file = srt_path.replace('.srt', '_fc.txt')

    if not slot_intervals:
        # Fast path — plain subtitle burn, no emoji overlay
        cmd = [
            'ffmpeg', '-y', '-loglevel', 'verbose', '-i', video_path,
            '-vf', subtitle_filter,
            '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'copy',
            output_path,
        ]
    else:
        unique = list(slot_intervals.items())

        inputs = ['-i', video_path]
        for (img, _slot), _ivs in unique:
            inputs += ['-i', img]

        parts = [
            f"[0:v]{subtitle_filter}[v0]"
        ]
        cur = 'v0'
        for i, ((img, slot), intervals) in enumerate(unique):
            scaled = f'em{i}'
            nxt    = f'v{i+1}'
            x      = f'(W-{em_px})/2+{slot*(em_px+em_gap)}'
            enable = '+'.join(f'between(t,{s},{e})' for s, e in intervals)
            parts.append(f'[{i+1}:v]scale={em_px}:{em_px}[{scaled}]')
            parts.append(
                f"[{cur}][{scaled}]overlay=x={x}:y={em_y}:enable='{enable}'[{nxt}]"
            )
            cur = nxt

        filter_content = ';'.join(parts)
        Path(filter_file).write_text(filter_content, encoding='utf-8')
        log.info("ffmpeg filter_complex_script %s:\n%s", filter_file, filter_content)

        cmd = ['ffmpeg', '-y', '-loglevel', 'verbose'] + inputs + [
            '-filter_complex_script', filter_file,
            '-map', f'[{cur}]', '-map', '0:a?',
            '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'copy',
            output_path,
        ]

    log.info("ffmpeg command: %s", subprocess.list2cmdline(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    log.info("ffmpeg returncode=%s", result.returncode)
    log.info("ffmpeg stderr:\n%s", result.stderr)
    if result.stdout:
        log.info("ffmpeg stdout:\n%s", result.stdout)

    if os.path.exists(filter_file):
        os.remove(filter_file)

    if result.returncode != 0:
        raise RuntimeError(f'FFmpeg failed:\n{result.stderr}')

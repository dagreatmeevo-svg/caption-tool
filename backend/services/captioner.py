import os
import re
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path

_DIR      = os.path.dirname(__file__)
_FONT_DIR = os.path.join(_DIR, "..", "fonts")
_EMOJI_CACHE = os.path.join(_DIR, "..", "emoji_cache")

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


def burn_subtitles(
    video_path: str,
    srt_path:   str,
    output_path: str,
    font_size:  int = 22,
    segments:   list[dict] | None = None,   # original segments (with emojis) for overlay
):
    srt_esc   = srt_path.replace('\\', '/').replace(':', '\\:')
    # fontsdir must be the DIRECTORY containing the font file, not the file itself
    fonts_dir = os.path.normpath(_FONT_DIR).replace('\\', '/').replace(':', '\\:')

    style = (
        f'FontName=Cairo,'
        f'FontSize={font_size},'
        f'Alignment=2,'
        f'MarginV=35,'
        f'PrimaryColour=&H00FFFFFF,'
        f'OutlineColour=&H00000000,'
        f'BorderStyle=1,'
        f'Outline=2,'
        f'Shadow=0'
    )

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
            'ffmpeg', '-y', '-i', video_path,
            '-vf', f"subtitles='{srt_esc}':fontsdir='{fonts_dir}':force_style='{style}'",
            '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'copy',
            output_path,
        ]
    else:
        unique = list(slot_intervals.items())

        inputs = ['-i', video_path]
        for (img, _slot), _ivs in unique:
            inputs += ['-i', img]

        parts = [
            f"[0:v]subtitles='{srt_esc}':fontsdir='{fonts_dir}':force_style='{style}'[v0]"
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

        Path(filter_file).write_text(';'.join(parts), encoding='utf-8')

        cmd = ['ffmpeg', '-y'] + inputs + [
            '-filter_complex_script', filter_file,
            '-map', f'[{cur}]', '-map', '0:a?',
            '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'copy',
            output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if os.path.exists(filter_file):
        os.remove(filter_file)

    if result.returncode != 0:
        raise RuntimeError(f'FFmpeg failed:\n{result.stderr}')

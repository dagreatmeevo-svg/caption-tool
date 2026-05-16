import os
import re
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path

_DIR = os.path.dirname(__file__)
_FONT_DIR = os.path.join(_DIR, "..", "fonts")
_EMOJI_CACHE = os.path.join(_DIR, "..", "emoji_cache")

# Matches common single-codepoint emoji characters
_EMOJI_RE = re.compile(
    '['
    '\U0001F300-\U0001F9FF'
    '\U0001FA00-\U0001FAFF'
    '\U00002600-\U000027BF'
    '\U0001F004'
    '\U0001F0CF'
    ']'
)


def _strip_emojis(text: str) -> str:
    return _EMOJI_RE.sub('', text).strip()


def _find_emojis(text: str) -> list[str]:
    return _EMOJI_RE.findall(text)


def _twemoji_png(char: str) -> str | None:
    """Download Twemoji 72x72 PNG for a single emoji char, return local path."""
    os.makedirs(_EMOJI_CACHE, exist_ok=True)
    cp = f'{ord(char):x}'
    dest = os.path.join(_EMOJI_CACHE, f'{cp}.png')
    if not os.path.exists(dest):
        url = f'https://cdn.jsdelivr.net/npm/twemoji@14.0.2/assets/72x72/{cp}.png'
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception:
            return None
    return dest if os.path.exists(dest) else None


def _parse_srt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding='utf-8').replace('\r\n', '\n')
    segments = []
    for block in re.split(r'\n{2,}', text.strip()):
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        try:
            t0, t1 = lines[1].split(' --> ')
            segments.append({
                'start': _ts(t0.strip()),
                'end':   _ts(t1.strip()),
                'text':  ' '.join(lines[2:]).strip(),
            })
        except Exception:
            continue
    return segments


def _ts(s: str) -> float:
    h, m, sec = s.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + float(sec)


def _fmt(s: float) -> str:
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    ms = round((s % 1) * 1000)
    return f'{h:02d}:{m:02d}:{sec:02d},{ms:03d}'


def _write_clean_srt(segments: list[dict], path: str):
    """Write SRT with emojis stripped — safe to render with Cairo font."""
    out, i = [], 1
    for seg in segments:
        text = _strip_emojis(seg['text'])
        if text:
            out.append(f'{i}\n{_fmt(seg["start"])} --> {_fmt(seg["end"])}\n{text}\n')
            i += 1
    Path(path).write_text('\n'.join(out), encoding='utf-8')


def burn_subtitles(video_path: str, srt_path: str, output_path: str, font_size: int = 22):
    segments = _parse_srt(srt_path)
    clean_srt = srt_path.replace('.srt', '_clean.srt')
    _write_clean_srt(segments, clean_srt)

    # Group emoji overlays: (img_path, slot_index) → list of (start, end)
    # Deduplication means the same emoji at the same slot uses ONE overlay filter
    # with a combined enable expression — avoids redundant ffmpeg filter stages.
    slot_intervals: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for seg in segments:
        for slot, ch in enumerate(_find_emojis(seg['text'])):
            img = _twemoji_png(ch)
            if img:
                slot_intervals[(img, slot)].append((seg['start'], seg['end']))

    srt_esc   = clean_srt.replace('\\', '/').replace(':', '\\:')
    fonts_dir = _FONT_DIR.replace('\\', '/').replace(':', '\\:')

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

    em_px = max(28, min(80, int(font_size * 2.0)))   # emoji image size in pixels
    em_gap = 8
    # Place emoji strip just above the subtitle text
    em_y = f'H-35-{int(font_size * 1.9)}-{em_px}-8'

    filter_file = srt_path.replace('.srt', '_fc.txt')
    cleanup = [clean_srt, filter_file]

    if not slot_intervals:
        # Fast path: no emojis, single -vf pass
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

        # Write filter graph to file — avoids Windows command-line length limit
        Path(filter_file).write_text(';'.join(parts), encoding='utf-8')

        cmd = ['ffmpeg', '-y'] + inputs + [
            '-filter_complex_script', filter_file,
            '-map', f'[{cur}]', '-map', '0:a?',
            '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'copy',
            output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    for f in cleanup:
        if os.path.exists(f):
            os.remove(f)

    if result.returncode != 0:
        raise RuntimeError(f'FFmpeg failed:\n{result.stderr}')

import re

# Comprehensive emoji strip — covers all major ranges + variation selectors + ZWJ
_EMOJI_RE = re.compile(
    '['
    '\U0001F300-\U0001F9FF'   # emoticons, symbols, pictographs
    '\U0001FA00-\U0001FAFF'   # symbols & pictographs extended-A
    '\U0001F1E0-\U0001F1FF'   # regional indicator letters (flags)
    '\U00002600-\U000027BF'   # misc symbols, dingbats
    '\U00002B00-\U00002BFF'   # misc symbols & arrows (⭐ etc.)
    '\U00002300-\U000023FF'   # misc technical
    '\U00002500-\U000025FF'   # box drawing / geometric shapes
    '\U0000FE00-\U0000FE0F'   # variation selectors (FE0F is the key one)
    '\U0001F004'
    '\U0001F0CF'
    '\U0000200D'              # zero-width joiner (used in ZWJ sequences)
    '\U000020E3'              # combining enclosing keycap
    ']+'
)


def _strip_emojis(text: str) -> str:
    return _EMOJI_RE.sub('', text).strip()


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _fmt_time(seg["start"])
        end   = _fmt_time(seg["end"])
        text  = _strip_emojis(seg["text"])
        if not text:
            continue
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def write_srt(segments: list[dict], path: str):
    content = build_srt(segments)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

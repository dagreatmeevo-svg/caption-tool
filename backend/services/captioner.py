import os
import subprocess

# Path to Cairo font bundled with the app
_FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "fonts")
CAIRO_FONT = os.path.join(_FONT_DIR, "Cairo-Regular.ttf")


def burn_subtitles(video_path: str, srt_path: str, output_path: str, font_size: int = 22):
    """
    Burn Arabic SRT subtitles into video using FFmpeg.
    Uses Cairo font for correct Arabic glyph rendering.
    """
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    font_escaped = CAIRO_FONT.replace("\\", "/").replace(":", "\\:")

    style = (
        f"FontName=Cairo,"
        f"FontSize={font_size},"
        f"Alignment=2,"          # bottom center
        f"MarginV=35,"
        f"PrimaryColour=&H00FFFFFF,"   # white text
        f"OutlineColour=&H00000000,"   # black outline
        f"BorderStyle=1,"
        f"Outline=2,"
        f"Shadow=0"
    )

    vf = f"subtitles='{srt_escaped}':fontsdir='{font_escaped}':force_style='{style}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", "18",           # near-lossless quality
        "-preset", "fast",
        "-c:a", "copy",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr}")

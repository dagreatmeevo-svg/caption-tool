# Handoff Summary — Caption Tool

## What this project does

Arabic caption tool. Web UI accepts a video (file upload or TikTok/YouTube/Instagram URL),
transcribes the audio, translates to Arabic, burns Arabic subtitles into the video,
and returns the captioned MP4.

**Stack:** FastAPI backend + static HTML/Tailwind frontend, all served from `:8000`.
**APIs:** Groq Whisper (transcription) + DeepSeek (translation).

## Pipeline

```
video → ffmpeg extract audio → Groq Whisper (verbose_json segments)
      → DeepSeek translate to Arabic (optionally adds emojis)
      → SRT file → ffmpeg subtitles filter (libass + Cairo font) → output.mp4
```

## Key files

| File | Purpose |
|---|---|
| [backend/main.py](backend/main.py) | FastAPI app, `/process` endpoint, job state, SSE progress |
| [backend/services/transcriber.py](backend/services/transcriber.py) | Groq Whisper, splits >25MB audio into chunks |
| [backend/services/translator.py](backend/services/translator.py) | DeepSeek `deepseek-chat`, batches 80 segments/call, optional emoji injection |
| [backend/services/srt_builder.py](backend/services/srt_builder.py) | Writes SRT, strips non-Cairo-renderable chars via allowlist |
| [backend/services/captioner.py](backend/services/captioner.py) | ffmpeg burn — subtitles filter + optional Twemoji PNG overlay via filter_complex |
| [backend/services/downloader.py](backend/services/downloader.py) | yt-dlp wrapper |
| [frontend/index.html](frontend/index.html) | Single-page RTL Arabic UI; tabs (URL/file), font-size slider+presets, emoji yes/no toggle, SSE progress, download |

## Features built

- Upload OR paste URL (yt-dlp downloads)
- Font size: slider 10–60 + 4 preset chips (16/22/34/48 px) — wired to ffmpeg `FontSize` via `force_style`
- Emoji toggle (👍 yes / 👎 no): when ON, DeepSeek is prompted to append 1–2 emojis per segment; captioner is supposed to overlay Twemoji PNGs from jsDelivr CDN above the Arabic text
- 6-step SSE progress UI (download → extract → transcribe → translate → burn → done)
- Git repo: https://github.com/dagreatmeevo-svg/caption-tool — `Procfile` (Railway) + `vercel.json` (frontend) already configured

## THE PROBLEM (unsolved)

**Two issues, both reported by the user after testing real videos:**

### 1. □ box still appears in the burned Arabic subtitles

User screenshot showed a □ glyph before the Arabic text (e.g. `□ لا بأس، كان يومًا`).
We've tried THREE fixes, all failed:

- **Attempt 1:** Strip emojis with a regex in `captioner.py` before burning (blocklist of common emoji ranges) — failed, □ persisted
- **Attempt 2:** Expanded the regex to cover more ranges (U+2B00-2BFF, variation selectors U+FE0F, ZWJ U+200D) and moved stripping to `srt_builder.py` — failed
- **Attempt 3 (current):** Replaced blocklist with **allowlist** — only Arabic (U+0600-06FF + presentation forms FB50-FDFF + FE70-FEFF), Latin (0020-007E, 00A0-024F) pass through. Unit-tested with `_safe_for_cairo` — confirmed strips ⭐, ❤️ (incl. VS-16), 😊, 👍, 😂. User STILL reports □.

**Possible real root causes we haven't ruled out:**
- The font isn't loading at all → libass falls back to a non-Arabic font → every char renders as □. We changed `fontsdir` from font-file-path to font-directory-path; the ORIGINAL code passed the font FILE path to `fontsdir` (technically wrong but apparently worked). Worth reverting to test.
- libass on Windows has Arabic-rendering quirks (shaping issues, missing libharfbuzz)
- Path escaping for `fontsdir` on Windows (the `\:` colon escape) may be malformed
- We never actually log the ffmpeg command or the SRT content — so we don't *know* what's reaching libass

### 2. Subtitles are "not accurate"

Vague — could mean:
- **Transcription quality:** Groq Whisper call has NO `language=` param, so it auto-detects. If the video is Arabic, Whisper transcribes Arabic → then DeepSeek "translates" Arabic→Arabic, which paraphrases/garbles the text
- **Translation quality:** DeepSeek prompt is generic, no Iraqi/Gulf dialect guidance
- **Timing:** segments may be too long/short for readability

## What needs to happen

1. **Instrument first, fix second.** Add logging to:
   - Print the final SRT content (UTF-8, with hex codepoints of any non-ASCII char) right before ffmpeg runs
   - Print the exact ffmpeg command and its stderr output
   - Use `ffmpeg -loglevel verbose` so libass font-loading errors are visible
2. **Test the font-loading hypothesis:** revert `fontsdir` to the font-file path (the way the original working code had it in [captioner.py](backend/services/captioner.py)) and see if □ disappears. If yes → it's a fontsdir bug. If no → libass isn't rendering Arabic correctly.
3. **Fallback rendering path:** if libass is the problem, render subtitles with Pillow (PIL) — full control, no font-discovery issues. Generate frame-by-frame text images and overlay with ffmpeg, OR pre-render the subtitle track as a transparent video and `-filter_complex` overlay it.
4. **Accuracy fix:**
   - Add a source-language selector to the frontend (Arabic / English / Auto)
   - Pass `language=` to the Groq call in [transcriber.py:_transcribe_file()](backend/services/transcriber.py)
   - When source is Arabic, **skip** the DeepSeek translation step entirely and pass Whisper segments straight to the SRT builder
   - Tighten DeepSeek prompt: keep meaning verbatim, prefer Iraqi/Gulf MSA, max 8 words per segment

## Environment

- Windows 10, Python 3.14, ffmpeg 8.1.1 (gyan.dev build), PowerShell
- `backend/.env` has `GROQ_API_KEY` and `DEEPSEEK_API_KEY` set
- Run: `cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000`
- Cairo font: [backend/fonts/Cairo-Regular.ttf](backend/fonts/Cairo-Regular.ttf)
- Test the allowlist works: `python -c "from services.srt_builder import _safe_for_cairo; print(_safe_for_cairo('hi 😊 ⭐ ❤️'))"`

## Recent commits

```
3acac30 Nuclear fix: allowlist-only chars Cairo can render — impossible for emoji to slip through
595a97f Fix □ box: strip emojis at SRT source, pass segments to captioner for Twemoji overlay
4f33673 Fix emoji rendering: strip from Cairo SRT, overlay Twemoji PNGs via ffmpeg filter_complex
1893fd4 Initial commit — Arabic caption tool
```

## What Codex should do first

1. Reproduce the bug: run the server, process a short Arabic video with emoji toggle ON
2. Add the logging in step 1 above. Look at the actual SRT bytes and the ffmpeg stderr.
3. Based on the log, you'll know whether it's a font-loading issue, an Arabic-shaping issue, or something we missed in the allowlist. Don't keep stabbing at the regex blind.

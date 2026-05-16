import json
from openai import OpenAI

BATCH_SIZE = 80  # segments per DeepSeek call


def _translate_batch(client: OpenAI, segments: list[dict], use_emoji: bool = False) -> list[dict]:
    """Translate a batch of segments to Arabic via DeepSeek. Returns segments with Arabic text."""
    payload = [{"id": i, "text": s["text"]} for i, s in enumerate(segments)]

    emoji_instruction = (
        " Add 1-2 relevant emojis at the end of each translated segment to match the tone or content."
        if use_emoji else ""
    )

    response = client.chat.completions.create(
        model="deepseek-chat",
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional Arabic translator for video subtitles. "
                    "Translate the given JSON array of subtitle segments to Arabic. "
                    "Keep translations natural and colloquial — this is for video captions."
                    + emoji_instruction +
                    " Preserve the exact JSON structure with 'id' and 'text' fields. "
                    "Return ONLY the JSON array, no explanation, no markdown."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown code fences if DeepSeek wraps in them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    translated = json.loads(raw)
    # Map back by id
    id_to_text = {item["id"]: item["text"] for item in translated}

    result = []
    for i, seg in enumerate(segments):
        result.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": id_to_text.get(i, seg["text"]),  # fallback to original if missing
        })
    return result


def translate_to_arabic(segments: list[dict], api_key: str, use_emoji: bool = False) -> list[dict]:
    """Translate all segments to Arabic in batches."""
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    translated_all = []
    for i in range(0, len(segments), BATCH_SIZE):
        batch = segments[i : i + BATCH_SIZE]
        translated_all.extend(_translate_batch(client, batch, use_emoji=use_emoji))

    return translated_all

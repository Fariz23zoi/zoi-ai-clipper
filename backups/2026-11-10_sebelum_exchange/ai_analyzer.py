import json
import re

from .ai_providers import call_claude_llm, call_chatgpt_llm


def build_prompt(transcript, num_clips=5):
    segments = transcript.get("segments", [])

    compact_segments = [
        {
            "start": round(float(s.get("start", 0)), 2),
            "end": round(float(s.get("end", 0)), 2),
            "text": str(s.get("text", "")).strip()
        }
        for s in segments
        if str(s.get("text", "")).strip()
    ]

    transcript_data = json.dumps(
        compact_segments,
        ensure_ascii=False,
        separators=(",", ":")
    )

    return f"""
Kamu adalah analis profesional video pendek YouTube Shorts.

Tugas:
Analisis transkrip berikut secara independen.
Pilih {num_clips} momen paling menarik untuk dijadikan video Shorts.

Prioritaskan:
1. Hook yang membuat penonton penasaran.
2. Konflik, kejutan, emosi, atau kejadian menarik.
3. Payoff atau penyelesaian yang memuaskan.
4. Konteks cerita harus utuh dan mudah dipahami.
5. Potensi membuat penonton menonton sampai selesai.

ATURAN TIMESTAMP:
- Semua waktu menggunakan detik.
- start_time dan end_time harus sesuai rentang waktu transkrip.
- Durasi setiap kandidat 30 sampai 90 detik.
- Jangan mengarang dialog atau kejadian.
- Hindari kandidat yang terlalu tumpang tindih.
- Pilih momen terbaik, bukan sekadar bagian yang memiliki kata menarik.

FORMAT OUTPUT:
Keluarkan JSON valid saja, tanpa markdown dan tanpa penjelasan di luar JSON.

{{
  "highlights": [
    {{
      "title": "Judul singkat kandidat",
      "start_time": 120.5,
      "end_time": 175.5,
      "hook_sentence": "Kalimat hook",
      "virality_reason": "Alasan momen ini menarik",
      "score": 90
    }}
  ]
}}

TRANSKRIP:
{transcript_data}
"""


def analyze_with_ai(transcript, provider="claude", num_clips=5):
    if provider not in ("claude", "chatgpt"):
        raise ValueError("Provider harus claude atau chatgpt.")

    segments = transcript.get("segments", [])
    if not segments:
        return {"highlights": []}

    prompt = build_prompt(transcript, num_clips)

    if provider == "claude":
        raw = call_claude_llm(prompt)
    else:
        raw = call_chatgpt_llm(prompt)

    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Respons {provider} bukan JSON valid: {cleaned[:500]}"
        ) from error

    highlights = result.get("highlights", [])
    if not isinstance(highlights, list):
        raise RuntimeError("Format highlights dari AI tidak valid.")

    video_duration = float(
        transcript.get("duration")
        or max(float(s.get("end", 0)) for s in segments)
    )

    validated = []

    for item in highlights:
        try:
            start = float(item["start_time"])
            end = float(item["end_time"])
        except (KeyError, TypeError, ValueError):
            continue

        duration = end - start

        if start < 0 or end > video_duration + 1:
            continue
        if duration < 30 or duration > 90:
            continue

        score = max(0, min(100, int(item.get("score", 0))))

        validated.append({
            "title": str(item.get("title", "Kandidat AI"))[:100],
            "start_time": round(start, 2),
            "end_time": round(end, 2),
            "duration": round(duration, 2),
            "score": score,
            "hook_sentence": str(item.get("hook_sentence", ""))[:250],
            "virality_reason": str(
                item.get("virality_reason", "")
            )[:500],
        })

    validated.sort(key=lambda item: item["score"], reverse=True)

    selected = []
    for item in validated:
        overlap_found = False

        for old in selected:
            overlap = max(
                0,
                min(item["end_time"], old["end_time"])
                - max(item["start_time"], old["start_time"])
            )
            shorter = min(item["duration"], old["duration"])

            if shorter > 0 and overlap / shorter >= 0.40:
                overlap_found = True
                break

        if not overlap_found:
            selected.append(item)

        if len(selected) >= num_clips:
            break

    return {"highlights": selected}

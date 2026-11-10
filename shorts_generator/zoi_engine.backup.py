import re

HOOK = [
    "ternyata", "rahasia", "kenapa", "mengapa",
    "bagaimana", "faktanya", "jangan", "kesalahan",
    "akhirnya", "bayangkan", "percaya"
]

EMOTION = [
    "takut", "sedih", "marah", "senang",
    "menangis", "tertawa", "terkejut",
    "kecewa", "bahagia", "menyesal"
]

CONFLICT = [
    "masalah", "konflik", "melawan", "gagal",
    "berjuang", "tantangan", "namun", "tetapi",
    "sulit", "kesulitan", "perjuangan"
]

VALUE = [
    "tips", "pelajaran", "manfaat", "solusi",
    "caranya", "penting", "belajar",
    "alasan", "cara", "fakta", "hasil"
]


def count_words(text, words):
    text = text.lower()
    return sum(
        1 for word in words
        if re.search(r"\b" + re.escape(word) + r"\b", text)
    )


def score_text(text, duration):
    score = 30

    score += min(count_words(text, HOOK) * 7, 21)
    score += min(count_words(text, EMOTION) * 6, 18)
    score += min(count_words(text, CONFLICT) * 5, 15)
    score += min(count_words(text, VALUE) * 4, 12)

    if "?" in text:
        score += 3

    if "!" in text:
        score += 2

    if 45 <= duration <= 75:
        score += 5

    word_count = len(text.split())

    if word_count >= 35:
        score += 4

    return min(100, score)


def find_hook(text):
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    for sentence in sentences:
        if count_words(sentence, HOOK) > 0:
            return sentence[:200]

    return sentences[0][:200] if sentences else text[:200]


def analyze_transcript(transcript, num_clips=5):
    segments = transcript.get("segments", [])

    if not segments:
        return {"highlights": []}

    candidates = []

    for i, first in enumerate(segments):
        start = float(first.get("start", 0))
        selected = []

        for seg in segments[i:]:
            selected.append(seg)
            end = float(seg.get("end", start))

            if end - start >= 60:
                break

            if end - start >= 90:
                break

        if not selected:
            continue

        end = float(selected[-1].get("end", start))
        duration = end - start

        if duration < 30 or duration > 90:
            continue

        text = " ".join(
            str(s.get("text", "")).strip()
            for s in selected
        ).strip()

        if len(text.split()) < 20:
            continue

        candidates.append({
            "title": text[:75],
            "start_time": round(start, 2),
            "end_time": round(end, 2),
            "score": score_text(text, duration),
            "hook_sentence": find_hook(text),
            "virality_reason": (
                "Dinilai berdasarkan kekuatan hook, emosi, "
                "konflik, nilai informasi, dan durasi."
            )
        })

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    chosen = []

    for item in candidates:
        overlap = any(
            min(item["end_time"], old["end_time"])
            > max(item["start_time"], old["start_time"])
            for old in chosen
        )

        if not overlap:
            chosen.append(item)

        if len(chosen) >= num_clips:
            break

    return {"highlights": chosen}

import re
from collections import Counter


HOOK = [
    "ternyata", "rahasia", "kenapa", "mengapa",
    "bagaimana", "faktanya", "bayangkan", "percaya",
    "jangan", "kesalahan", "akhirnya", "serius",
    "gila", "aneh", "mustahil"
]

EMOTION = [
    "takut", "sedih", "marah", "senang", "menangis",
    "tertawa", "terkejut", "kecewa", "bahagia",
    "menyesal", "kesal", "panik", "bingung",
    "kaget", "lucu", "malu"
]

CONFLICT = [
    "masalah", "konflik", "melawan", "gagal",
    "berjuang", "tantangan", "sulit", "kesulitan",
    "ribut", "debat", "salah", "keliru",
    "rusak", "tidak bisa", "enggak bisa",
    "nggak bisa", "tetapi", "namun"
]

PAYOFF = [
    "akhirnya", "ternyata", "berhasil", "selesai",
    "jadi", "hasilnya", "rupanya", "terbukti",
    "jawabannya", "solusinya", "beres", "ketemu"
]

VALUE = [
    "tips", "pelajaran", "manfaat", "solusi",
    "caranya", "penting", "belajar", "alasan",
    "cara", "fakta", "hasil", "pengalaman",
    "contohnya", "ingat"
]

REACTION = [
    "hahaha", "wkwk", "anjir", "anjay", "gila",
    "serius", "astaga", "waduh", "buset",
    "ampun", "luar biasa", "keren"
]


def normalize(text):
    return re.sub(r"\s+", " ", str(text).lower()).strip()


def count_words(text, words):
    text = normalize(text)
    total = 0

    for word in words:
        pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
        if re.search(pattern, text):
            total += 1

    return total


def split_sentences(text):
    return [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", text)
        if s.strip()
    ]


def hook_score(text):
    sentences = split_sentences(text)

    if not sentences:
        return 0

    opening = sentences[0]
    score = 0

    score += min(count_words(opening, HOOK) * 12, 30)

    if "?" in opening:
        score += 12

    if "!" in opening:
        score += 8

    if len(opening.split()) <= 15:
        score += 8

    if count_words(opening, REACTION):
        score += 10

    return min(score, 60)


def emotion_score(text):
    score = count_words(text, EMOTION) * 7
    score += count_words(text, REACTION) * 8

    if "!" in text:
        score += 5

    return min(score, 40)


def conflict_score(text):
    score = count_words(text, CONFLICT) * 7

    if "?" in text:
        score += 5

    if "tapi" in normalize(text) or "namun" in normalize(text):
        score += 8

    return min(score, 40)


def payoff_score(text):
    score = count_words(text, PAYOFF) * 8
    return min(score, 35)


def value_score(text):
    score = count_words(text, VALUE) * 7
    return min(score, 30)


def storytelling_score(text):
    sentences = split_sentences(text)

    score = 0

    if len(sentences) >= 3:
        score += 10

    if count_words(text, CONFLICT):
        score += 10

    if count_words(text, PAYOFF):
        score += 10

    if len(text.split()) >= 50:
        score += 10

    return min(score, 40)


def score_text(text, duration):
    hook = hook_score(text)
    emotion = emotion_score(text)
    conflict = conflict_score(text)
    payoff = payoff_score(text)
    value = value_score(text)
    story = storytelling_score(text)

    duration_score = 0

    if 45 <= duration <= 75:
        duration_score = 10
    elif 30 <= duration <= 90:
        duration_score = 5

    raw_score = (
        hook * 0.25
        + emotion * 0.18
        + conflict * 0.18
        + payoff * 0.14
        + value * 0.10
        + story * 0.15
    )

    return min(100, round(raw_score + duration_score))


def find_hook(text):
    sentences = split_sentences(text)

    if not sentences:
        return text[:200]

    best_sentence = sentences[0]
    best_score = -1

    for sentence in sentences[:5]:
        score = hook_score(sentence)

        if "?" in sentence:
            score += 5

        if len(sentence.split()) <= 15:
            score += 3

        if score > best_score:
            best_score = score
            best_sentence = sentence

    return best_sentence[:200]


def make_reason(text, duration):
    reasons = []

    if hook_score(text) >= 20:
        reasons.append("memiliki pembuka yang menarik")

    if emotion_score(text) >= 15:
        reasons.append("mengandung reaksi emosional")

    if conflict_score(text) >= 15:
        reasons.append("memiliki unsur konflik")

    if payoff_score(text) >= 8:
        reasons.append("memiliki bagian penyelesaian atau kejutan")

    if value_score(text) >= 8:
        reasons.append("mengandung informasi atau pelajaran")

    if storytelling_score(text) >= 20:
        reasons.append("memiliki alur percakapan")

    if not reasons:
        reasons.append("memiliki percakapan yang cukup utuh")

    return "Kandidat dipilih karena " + ", ".join(reasons) + "."


def analyze_transcript(transcript, num_clips=5):
    segments = transcript.get("segments", [])

    if not segments:
        return {"highlights": []}

    candidates = []

    for i in range(len(segments)):
        start = float(segments[i].get("start", 0))
        selected = []

        for seg in segments[i:]:
            selected.append(seg)
            end = float(seg.get("end", start))

            if end - start >= 60:
                break

        if not selected:
            continue

        end = float(selected[-1].get("end", start))
        duration = end - start

        if duration < 30 or duration > 90:
            continue

        text = " ".join(
            str(seg.get("text", "")).strip()
            for seg in selected
        ).strip()

        if len(text.split()) < 20:
            continue

        score = score_text(text, duration)

        if score < 15:
            continue

        candidates.append({
            "title": text[:75],
            "start_time": round(start, 2),
            "end_time": round(end, 2),
            "score": score,
            "hook_sentence": find_hook(text),
            "virality_reason": make_reason(text, duration)
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

import re


HOOK_WORDS = [
    "ternyata", "rahasia", "kenapa", "mengapa",
    "bagaimana", "faktanya", "bayangkan", "percaya",
    "jangan", "kesalahan", "akhirnya", "serius",
    "gila", "aneh", "mustahil", "kok"
]

CONFLICT_WORDS = [
    "masalah", "konflik", "melawan", "gagal",
    "berjuang", "tantangan", "sulit", "kesulitan",
    "ribut", "debat", "salah", "keliru",
    "rusak", "tidak bisa", "enggak bisa",
    "nggak bisa", "tapi", "namun", "kenapa"
]

PAYOFF_WORDS = [
    "akhirnya", "ternyata", "berhasil", "selesai",
    "hasilnya", "rupanya", "terbukti",
    "jawabannya", "solusinya", "beres",
    "ketemu", "jadi", "ternyata"
]

REACTION_WORDS = [
    "hahaha", "wkwk", "anjir", "anjay",
    "astaga", "waduh", "buset", "ampun",
    "tertawa", "kaget", "serius", "gila",
    "luar biasa", "keren"
]


def normalize(text):
    return re.sub(r"\s+", " ", str(text).lower()).strip()


def count_words(text, words):
    text = normalize(text)
    return sum(
        1 for word in words
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text)
    )


def split_sentences(text):
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+", text)
        if item.strip()
    ]


def section_scores(text):
    sentences = split_sentences(text)

    if not sentences:
        sentences = [text] if text else []

    if not sentences:
        return {"hook": 0, "conflict": 0, "payoff": 0}

    opening = " ".join(sentences[:3])
    middle_start = len(sentences) // 3
    middle_end = max(middle_start + 1, (len(sentences) * 2) // 3)
    middle = " ".join(sentences[middle_start:middle_end])
    ending = " ".join(sentences[-3:])

    hook = 0
    hook += min(count_words(opening, HOOK_WORDS) * 12, 30)
    hook += min(opening.count("?") * 8, 16)
    hook += min(count_words(opening, REACTION_WORDS) * 8, 16)

    if len(opening.split()) >= 4:
        hook += 8

    conflict = 0
    conflict += min(count_words(middle, CONFLICT_WORDS) * 8, 32)
    conflict += min(middle.count("?") * 5, 15)
    conflict += min(count_words(middle, REACTION_WORDS) * 7, 21)

    if "tapi" in normalize(middle) or "namun" in normalize(middle):
        conflict += 8

    payoff = 0
    payoff += min(count_words(ending, PAYOFF_WORDS) * 10, 30)
    payoff += min(count_words(ending, REACTION_WORDS) * 9, 27)
    payoff += min(ending.count("!") * 6, 12)

    if len(ending.split()) >= 8:
        payoff += 8

    return {
        "hook": min(hook, 70),
        "conflict": min(conflict, 70),
        "payoff": min(payoff, 70)
    }


def score_candidate(text, duration):
    parts = section_scores(text)

    words = len(text.split())
    sentence_count = len(split_sentences(text))

    completeness = 0

    if sentence_count >= 3:
        completeness += 10

    if words >= 50:
        completeness += 10

    if words >= 80:
        completeness += 5

    duration_score = 0

    if 45 <= duration <= 75:
        duration_score = 10
    elif 30 <= duration <= 90:
        duration_score = 5

    total = (
        parts["hook"] * 0.30
        + parts["conflict"] * 0.30
        + parts["payoff"] * 0.30
        + completeness * 0.10
        + duration_score
    )

    return min(100, round(total)), parts


def find_hook(text):
    sentences = split_sentences(text)

    if not sentences:
        return text[:150]

    best = sentences[0]
    best_score = -1

    for sentence in sentences[:5]:
        score = (
            count_words(sentence, HOOK_WORDS) * 10
            + sentence.count("?") * 8
            + count_words(sentence, REACTION_WORDS) * 8
        )

        if score > best_score:
            best = sentence
            best_score = score

    return best[:150]


def make_reason(parts):
    reasons = []

    if parts["hook"] >= 20:
        reasons.append("pembukaan berpotensi menarik perhatian")

    if parts["conflict"] >= 20:
        reasons.append("terdapat interaksi atau konflik")

    if parts["payoff"] >= 20:
        reasons.append("bagian akhir memiliki reaksi atau penyelesaian")

    if not reasons:
        reasons.append("memiliki percakapan yang cukup panjang")

    return "Kandidat dipilih karena " + ", ".join(reasons) + "."


def analyze_transcript(transcript, num_clips=5):
    segments = transcript.get("segments", [])

    if not segments:
        return {"highlights": []}

    candidates = []
    durations = [45, 60, 75]

    for i, first in enumerate(segments):
        start = float(first.get("start", 0))

        for target_duration in durations:
            selected = []

            for seg in segments[i:]:
                seg_start = float(seg.get("start", start))

                if seg_start - start > target_duration + 8:
                    break

                if selected:
                    previous_end = float(selected[-1].get("end", seg_start))
                    if seg_start - previous_end > 10:
                        break

                selected.append(seg)

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

            score, parts = score_candidate(text, duration)

            if score < 15:
                continue

            candidates.append({
                "title": text[:75],
                "start_time": round(start, 2),
                "end_time": round(end, 2),
                "duration": round(duration, 2),
                "score": score,
                "hook_score": parts["hook"],
                "conflict_score": parts["conflict"],
                "payoff_score": parts["payoff"],
                "hook_sentence": find_hook(text),
                "virality_reason": make_reason(parts)
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

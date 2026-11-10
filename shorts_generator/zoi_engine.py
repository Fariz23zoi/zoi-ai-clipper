import re

EVENT_WORDS = [
    "hahaha", "wkwk", "tertawa", "kaget", "serius",
    "anjir", "anjay", "astaga", "waduh", "buset",
    "kenapa", "mengapa", "kok", "ternyata",
    "berhasil", "akhirnya", "beneran", "masa"
]

CONFLICT_WORDS = [
    "masalah", "salah", "keliru", "gagal",
    "ribut", "debat", "sulit", "susah",
    "enggak bisa", "nggak bisa", "tidak bisa",
    "tapi", "namun", "kenapa"
]

PAYOFF_WORDS = [
    "akhirnya", "ternyata", "hasilnya", "rupanya",
    "berhasil", "selesai", "ketemu", "jawabannya",
    "beneran", "serius", "hahaha", "wkwk"
]

HOOK_WORDS = [
    "kenapa", "mengapa", "ternyata", "rahasia",
    "bagaimana", "bayangkan", "serius", "kok",
    "jangan", "gila", "mustahil", "percaya"
]


def normalize(text):
    return re.sub(r"\s+", " ", str(text).lower()).strip()


def count_words(text, words):
    text = normalize(text)
    return sum(
        1 for word in words
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text)
    )


def event_score(text):
    score = count_words(text, EVENT_WORDS) * 8

    if "?" in text:
        score += 8

    if "!" in text:
        score += 5

    if re.search(r"(rp\\s?\\d|\\d+\\s?ribu|\\d+\\s?juta)", normalize(text)):
        score += 12

    return min(score, 40)


def split_sentences(text):
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+", text)
        if item.strip()
    ]


def score_candidate(text, duration, anchor_score):
    sentences = split_sentences(text)

    opening = " ".join(sentences[:3])
    middle = " ".join(sentences[2:-2])
    ending = " ".join(sentences[-3:])

    hook = (
        count_words(opening, HOOK_WORDS) * 10
        + min(opening.count("?") * 8, 16)
        + min(event_score(opening), 20)
    )

    conflict = (
        count_words(middle, CONFLICT_WORDS) * 9
        + min(middle.count("?") * 5, 15)
        + min(count_words(middle, EVENT_WORDS) * 5, 20)
    )

    payoff = (
        count_words(text, PAYOFF_WORDS) * 8
        + min(count_words(text, EVENT_WORDS) * 7, 28)
        + min(text.count("!") * 5, 10)
    )

    if re.search(r"rp\s?\d|\d+\s?ribu|\d+\s?juta", normalize(text)):
        payoff += 15

    if any(word in normalize(text) for word in [
        "apa itu", "beneran", "kaget", "mahal", "serius"
    ]):
        payoff += 10

    completeness = 0

    if len(sentences) >= 3:
        completeness += 10

    if len(text.split()) >= 50:
        completeness += 10

    duration_score = 10 if 45 <= duration <= 75 else 5

    total = (
        hook * 0.25
        + conflict * 0.25
        + payoff * 0.25
        + min(anchor_score, 40) * 0.15
        + completeness * 0.10
        + duration_score
    )

    return min(100, round(total)), {
        "hook": min(hook, 70),
        "conflict": min(conflict, 70),
        "payoff": min(payoff, 70),
        "event": anchor_score
    }


def find_hook(text):
    sentences = split_sentences(text)

    if not sentences:
        return text[:150]

    return max(
        sentences[:5],
        key=lambda sentence: (
            count_words(sentence, HOOK_WORDS) * 10
            + sentence.count("?") * 8
            + count_words(sentence, EVENT_WORDS) * 5
        )
    )[:150]


def analyze_transcript(transcript, num_clips=5):
    segments = transcript.get("segments", [])

    if not segments:
        return {"highlights": []}

    candidates = []
    seen = set()

    for i, anchor in enumerate(segments):
        anchor_text = str(anchor.get("text", "")).strip()
        anchor_score = event_score(anchor_text)

        if anchor_score < 8:
            continue

        anchor_start = float(anchor.get("start", 0))
        start_target = max(0, anchor_start - 25)

        start_index = i

        while start_index > 0:
            previous = segments[start_index - 1]
            previous_end = float(previous.get("end", 0))
            next_start = float(segments[start_index].get("start", anchor_start))

            if next_start - previous_end > 10:
                break

            if float(previous.get("start", 0)) < start_target:
                break

            start_index -= 1

        start = float(segments[start_index].get("start", anchor_start))

        for target_duration in (45, 60, 75):
            selected = []

            for seg in segments[start_index:]:
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

            if not (start <= anchor_start <= end):
                continue

            text = " ".join(
                str(seg.get("text", "")).strip()
                for seg in selected
            ).strip()

            if len(text.split()) < 20:
                continue

            key = (round(start), round(end))

            if key in seen:
                continue

            seen.add(key)

            score, parts = score_candidate(
                text, duration, anchor_score
            )

            candidates.append({
                "title": text[:75],
                "start_time": round(start, 2),
                "end_time": round(end, 2),
                "duration": round(duration, 2),
                "score": score,
                "hook_score": parts["hook"],
                "conflict_score": parts["conflict"],
                "payoff_score": parts["payoff"],
                "event_score": parts["event"],
                "hook_sentence": find_hook(text),
                "virality_reason": (
                    "Kandidat dipilih berdasarkan momen penting, "
                    "konteks percakapan, dan penilaian alur."
                )
            })

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    chosen = []
    for item in candidates:
        duplicate = False
        for old in chosen:
            overlap = max(0, min(item["end_time"], old["end_time"]) - max(item["start_time"], old["start_time"]))
            shorter = min(item["duration"], old["duration"])
            if shorter > 0 and overlap / shorter >= 0.40:
                duplicate = True
                break
        if not duplicate:
            chosen.append(item)
        if len(chosen) >= num_clips:
            break

    return {"highlights": chosen}

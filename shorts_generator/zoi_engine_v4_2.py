"""ZOI Engine V4.2 (eksperimental).

V4.1 (zoi_engine.py) tetap menjadi baseline dan tidak diubah. V4.2 memakai
daftar kata dan fungsi hitung milik V4.1, lalu menambahkan:

1. Batas klip di awal dan akhir kalimat, memakai waktu per kata dari
   transkrip, sehingga klip tidak mulai atau berhenti di tengah kalimat.
2. Sinyal selain kata pemicu: reaksi penonton ([tertawa], [tepuk tangan]),
   tempo bicara yang naik, pertukaran dialog cepat, pertanyaan, angka.
3. Penilaian alur RELATE -> KONFLIK -> PAYOFF berdasarkan posisi waktu
   di dalam klip (awal, tengah, akhir), bukan berdasarkan nomor kalimat.
4. Judul dan alasan yang disusun dari sinyal yang benar-benar ditemukan.

Format keluaran sama dengan V4.1: {"highlights": [...]}.
"""
import re

from . import zoi_engine as v41

ENGINE_NAME = "ZOI ENGINE V4.2"

MIN_DURATION = 30.0
MAX_DURATION = 90.0
TARGET_DURATIONS = (45, 60, 75)
CONTEXT_BEFORE = 25.0
MAX_SENTENCE_SECONDS = 12.0
MAX_OVERLAP = 0.40

REACTION_PATTERN = re.compile(
    r"\[(?:tertawa|tawa|tepuk tangan|sorak|laughter|applause)[^\]]*\]",
    re.IGNORECASE
)
MONEY_PATTERN = re.compile(r"rp\s?\d|\d+\s?ribu|\d+\s?juta|\d+\s?miliar")
NOISE_PATTERN = re.compile(r"\[(?:musik|music)[^\]]*\]", re.IGNORECASE)


def build_sentences(transcript):
    """Susun kalimat bertimestamp dari kata, atau dari segmen bila kata
    tidak tersedia."""
    words = transcript.get("words") or []

    if not words:
        return [
            {
                "start": float(seg.get("start", 0)),
                "end": float(seg.get("end", 0)),
                "text": str(seg.get("text", "")).strip()
            }
            for seg in transcript.get("segments", [])
            if str(seg.get("text", "")).strip()
        ]

    punctuated = sum(
        1 for word in words if word["text"][-1:] in ".?!"
    ) >= max(3, len(words) // 60)

    sentences = []
    current = []

    def close():
        if current:
            sentences.append({
                "start": float(current[0]["start"]),
                "end": float(current[-1]["end"]),
                "text": " ".join(word["text"] for word in current)
            })
            current.clear()

    for index, word in enumerate(words):
        if current:
            gap = float(word["start"]) - float(current[-1]["end"])
            length = float(word["start"]) - float(current[0]["start"])

            if (
                gap > 1.5
                or length >= MAX_SENTENCE_SECONDS
                or (not punctuated and gap > 0.7)
            ):
                close()

        current.append(word)

        if punctuated and word["text"][-1:] in ".?!":
            close()

    close()
    return sentences


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def sentence_signals(sentence, median_rate):
    """Hitung sinyal satu kalimat. Mengembalikan (skor, daftar label)."""
    text = sentence["text"]
    lowered = v41.normalize(text)
    labels = []

    score = v41.count_words(text, v41.EVENT_WORDS) * 8
    if score:
        labels.append("kata_reaksi")

    if "?" in text:
        score += 8
        labels.append("pertanyaan")

    if "!" in text:
        score += 5

    if MONEY_PATTERN.search(lowered):
        score += 12
        labels.append("uang")
    elif re.search(r"\d", lowered):
        score += 4
        labels.append("angka")

    if REACTION_PATTERN.search(text):
        score += 10
        labels.append("reaksi")

    duration = max(0.3, sentence["end"] - sentence["start"])
    word_count = len(text.split())

    if median_rate and word_count >= 5:
        if word_count / duration >= median_rate * 1.35:
            score += 6
            labels.append("tempo_cepat")

    return min(score, 40), labels


def _part_text(sentences, start, end):
    return " ".join(
        s["text"] for s in sentences
        if start <= (s["start"] + s["end"]) / 2 < end
    )


def score_window(sentences, anchor_score, signal_labels):
    """Nilai satu kandidat berdasarkan posisi waktu di dalam klip."""
    start = sentences[0]["start"]
    end = sentences[-1]["end"]
    duration = end - start
    text = " ".join(s["text"] for s in sentences)

    opening = _part_text(sentences, start, start + duration * 0.25)
    middle = _part_text(
        sentences, start + duration * 0.25, start + duration * 0.70
    )
    ending = _part_text(sentences, start + duration * 0.70, end + 0.01)

    hook = (
        v41.count_words(opening, v41.HOOK_WORDS) * 10
        + min(opening.count("?") * 8, 16)
        + min(v41.count_words(opening, v41.EVENT_WORDS) * 6, 18)
    )

    conflict = (
        v41.count_words(middle, v41.CONFLICT_WORDS) * 9
        + min(middle.count("?") * 5, 15)
        + min(v41.count_words(middle, v41.EVENT_WORDS) * 5, 20)
    )

    # Pertukaran dialog cepat di bagian tengah menandakan ketegangan.
    short_turns = sum(
        1 for s in sentences
        if len(s["text"].split()) <= 4
        and start + duration * 0.25 <= s["start"] < start + duration * 0.70
    )
    conflict += min(short_turns * 3, 12)

    payoff = (
        v41.count_words(ending, v41.PAYOFF_WORDS) * 10
        + min(v41.count_words(ending, v41.EVENT_WORDS) * 6, 18)
        + min(len(REACTION_PATTERN.findall(ending)) * 10, 20)
        + min(ending.count("!") * 5, 10)
    )

    if MONEY_PATTERN.search(v41.normalize(text)):
        payoff += 10

    reactions = len(REACTION_PATTERN.findall(text))
    engagement = min(reactions * 6, 18) + min(
        signal_labels.count("tempo_cepat") * 3, 9
    )

    completeness = 0
    if len(sentences) >= 4:
        completeness += 10
    if len(text.split()) >= 60:
        completeness += 10
    if hook and payoff:
        completeness += 10

    duration_score = 10 if 40 <= duration <= 75 else 5

    noise = len(NOISE_PATTERN.findall(text)) * 4

    total = (
        min(hook, 60) * 0.30
        + min(conflict, 60) * 0.25
        + min(payoff, 60) * 0.30
        + min(anchor_score, 40) * 0.20
        + engagement * 0.60
        + completeness * 0.50
        + duration_score
        - noise
    )

    return max(0, min(100, round(total))), {
        "hook": min(hook, 70),
        "conflict": min(conflict, 70),
        "payoff": min(payoff, 70),
        "event": anchor_score,
        "reactions": reactions
    }


def find_hook(sentences):
    """Kalimat pembuka paling kuat dari 25% awal klip."""
    start = sentences[0]["start"]
    limit = start + (sentences[-1]["end"] - start) * 0.25
    opening = [s for s in sentences if s["start"] <= limit] or sentences[:1]

    best = max(
        opening,
        key=lambda s: (
            v41.count_words(s["text"], v41.HOOK_WORDS) * 10
            + s["text"].count("?") * 8
            + v41.count_words(s["text"], v41.EVENT_WORDS) * 5
            + min(len(s["text"].split()), 8)
        )
    )
    return best["text"][:150]


def build_reason(parts, labels, duration):
    reasons = []

    if parts["hook"] >= 10:
        reasons.append("pembuka memancing rasa penasaran")
    if labels.count("pertanyaan") >= 3:
        reasons.append("banyak tanya jawab di dalam klip")
    if parts["conflict"] >= 12:
        reasons.append("bagian tengah berisi ketegangan atau perdebatan")
    if parts["reactions"]:
        reasons.append(f"ada {parts['reactions']} reaksi tawa/tepuk tangan")
    if "uang" in labels:
        reasons.append("menyebut nominal uang")
    elif "angka" in labels:
        reasons.append("menyebut angka")
    if labels.count("tempo_cepat") >= 3:
        reasons.append("tempo bicara naik")
    if parts["payoff"] >= 10:
        reasons.append("ditutup dengan penyelesaian")

    if not reasons:
        reasons.append("momen paling menonjol di sekitar bagian ini")

    text = "; ".join(reasons)
    return (
        text[0].upper() + text[1:]
        + f". Durasi {round(duration)} detik, dipotong di batas kalimat."
    )


def make_title(hook_sentence):
    title = re.sub(r"\[[^\]]*\]", " ", hook_sentence)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")

    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0] + "..."

    return title or "Kandidat ZOI"


def analyze_transcript(transcript, num_clips=5):
    sentences = build_sentences(transcript)

    if not sentences:
        return {"highlights": []}

    rates = [
        len(s["text"].split()) / max(0.3, s["end"] - s["start"])
        for s in sentences
        if len(s["text"].split()) >= 5
    ]
    median_rate = _median(rates)

    signals = [sentence_signals(s, median_rate) for s in sentences]

    candidates = []
    seen = set()

    for anchor_index, (anchor_score, _) in enumerate(signals):
        if anchor_score < 8:
            continue

        anchor = sentences[anchor_index]

        # RELATE: mundur untuk mengambil konteks sebelum momen penting.
        start_index = anchor_index
        while start_index > 0:
            previous = sentences[start_index - 1]

            if sentences[start_index]["start"] - previous["end"] > 10:
                break
            if previous["start"] < anchor["start"] - CONTEXT_BEFORE:
                break

            start_index -= 1

        start = sentences[start_index]["start"]

        for target in TARGET_DURATIONS:
            end_index = start_index

            for index in range(start_index, len(sentences)):
                if index > start_index:
                    gap = sentences[index]["start"] - sentences[index - 1]["end"]
                    if gap > 10:
                        break

                if sentences[index]["end"] - start > target + 6:
                    break

                end_index = index

            selected = sentences[start_index:end_index + 1]
            end = selected[-1]["end"]
            duration = end - start

            if duration < MIN_DURATION or duration > MAX_DURATION:
                continue
            if end_index < anchor_index:
                continue

            text = " ".join(s["text"] for s in selected)
            if len(text.split()) < 20:
                continue

            key = (round(start), round(end))
            if key in seen:
                continue
            seen.add(key)

            labels = [
                label
                for _, sentence_labels in signals[start_index:end_index + 1]
                for label in sentence_labels
            ]

            score, parts = score_window(selected, anchor_score, labels)
            hook_sentence = find_hook(selected)

            candidates.append({
                "title": make_title(hook_sentence),
                "start_time": round(start, 2),
                "end_time": round(end, 2),
                "duration": round(duration, 2),
                "score": score,
                "hook_score": parts["hook"],
                "conflict_score": parts["conflict"],
                "payoff_score": parts["payoff"],
                "event_score": parts["event"],
                "hook_sentence": hook_sentence,
                "virality_reason": build_reason(parts, labels, duration)
            })

    candidates.sort(key=lambda item: item["score"], reverse=True)

    chosen = []
    for item in candidates:
        duplicate = False

        for old in chosen:
            overlap = max(
                0,
                min(item["end_time"], old["end_time"])
                - max(item["start_time"], old["start_time"])
            )
            shorter = min(item["duration"], old["duration"])

            if shorter > 0 and overlap / shorter >= MAX_OVERLAP:
                duplicate = True
                break

        if not duplicate:
            chosen.append(item)

        if len(chosen) >= num_clips:
            break

    return {"highlights": chosen}

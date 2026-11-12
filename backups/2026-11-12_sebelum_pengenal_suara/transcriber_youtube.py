import os
import re
import html
from pathlib import Path
from typing import Dict

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from ..config import LOCAL_OUTPUT_DIR


def _to_seconds(value):
    value = value.strip().split(" ")[0]
    parts = value.replace(",", ".").split(":")
    return sum(
        float(part) * (60 ** index)
        for index, part in enumerate(reversed(parts))
    )


def _clean_text(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


_TIMESTAMP = r"(?:\d+:)?\d{2}:\d{2}[.,]\d{3}"
_CUE_TIMING = re.compile(
    r"^\s*(" + _TIMESTAMP + r")\s*-->\s*(" + _TIMESTAMP + r")"
)
_INLINE_TIME = re.compile(r"<(" + _TIMESTAMP + r")>")


def _read_cues(content):
    """Pecah isi VTT menjadi daftar (start, end, baris-baris teks).

    Cue hanya diakhiri baris yang benar-benar kosong. Baris berisi spasi
    adalah bagian dari cue (YouTube memakainya sebagai baris kosong
    pada rolling caption).
    """
    cues = []
    current = None

    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        timing = _CUE_TIMING.match(line)

        if timing:
            current = []
            cues.append((
                _to_seconds(timing.group(1)),
                _to_seconds(timing.group(2)),
                current
            ))
        elif line == "":
            current = None
        elif current is not None:
            current.append(line)

    return cues


def _plain_words(text):
    """Bersihkan teks cue tanpa tag waktu lalu pecah menjadi kata."""
    text = _clean_text(text)
    # Buang simbol not. Keterangan seperti [tertawa] dipertahankan karena
    # menjadi sinyal bagi ZOI Engine.
    text = text.replace("\u266a", " ").replace("\u266b", " ")
    text = re.sub(r"\[\s*\]", " ", text)
    return text.split()


def _spread_words(words, start, end):
    """Bagi rata waktu cue untuk kata-kata yang tidak punya tag waktu."""
    if not words:
        return []

    step = max(0.0, end - start) / len(words)

    return [
        {
            "time": start + step * index,
            "end": start + step * (index + 1),
            "text": word
        }
        for index, word in enumerate(words)
    ]


def _timed_line_words(line, cue_start, cue_end):
    """Ambil kata dari baris bertag waktu milik subtitle otomatis.

    Bentuk barisnya: kata1<00:00:00.600><c> kata2</c><00:00:01.300><c> ...
    Kata sebelum tag pertama dimulai pada awal cue.
    """
    parts = _INLINE_TIME.split(line)
    chunks = [(cue_start, parts[0])]

    for index in range(1, len(parts) - 1, 2):
        chunks.append((_to_seconds(parts[index]), parts[index + 1]))

    words = []

    for index, (start, text) in enumerate(chunks):
        if index + 1 < len(chunks):
            end = chunks[index + 1][0]
        else:
            end = cue_end

        words.extend(
            _spread_words(_clean_text(text).split(), start, max(start, end))
        )

    return words


PARSER_VERSION = 2


def _parse_vtt_words(content):
    """Kembalikan daftar kata {time, end, text} berurutan dari isi VTT."""
    cues = _read_cues(content)

    # Subtitle otomatis YouTube memakai tag waktu per kata dan rolling
    # caption. Subtitle manual hanya berisi teks biasa.
    rolling = any(
        _INLINE_TIME.search(line)
        for _, _, lines in cues
        for line in lines
    )

    words = []

    for cue_start, cue_end, lines in cues:
        if not lines:
            continue

        if rolling:
            # Baris terakhir berisi teks baru. Baris sebelumnya adalah
            # ulangan cue lama, dan cue pengisi berakhir dengan baris kosong.
            line = lines[-1]

            if not line.strip():
                continue

            if _INLINE_TIME.search(line):
                words.extend(_timed_line_words(line, cue_start, cue_end))
            else:
                words.extend(
                    _spread_words(_plain_words(line), cue_start, cue_end)
                )
        else:
            words.extend(
                _spread_words(
                    _plain_words(" ".join(lines)), cue_start, cue_end
                )
            )

    words.sort(key=lambda item: item["time"])

    # Hapus duplikasi teknis pada timestamp yang hampir sama.
    unique_words = []
    for item in words:
        normalized = re.sub(
            r"[^a-z0-9]",
            "",
            item["text"].lower()
        )

        duplicate = any(
            normalized
            and normalized == re.sub(
                r"[^a-z0-9]",
                "",
                old["text"].lower()
            )
            and abs(item["time"] - old["time"]) <= 0.05
            for old in unique_words[-3:]
        )

        if not duplicate:
            unique_words.append(item)

    return unique_words


def _segments_from_words(unique_words):
    """Gabungkan kata menjadi segmen percakapan."""
    segments = []
    current_words = []
    segment_start = None
    previous_time = None
    previous_end = None

    def close_segment():
        segments.append({
            "start": round(segment_start, 2),
            "end": round(max(previous_end, previous_time), 2),
            "text": " ".join(current_words)
        })

    for item in unique_words:
        timestamp = item["time"]

        if current_words:
            gap = timestamp - previous_time
            duration = timestamp - segment_start

            if gap > 2.0 or duration >= 8.0:
                # Akhir segmen adalah akhir kata terakhir, tetapi tidak
                # boleh melewati awal segmen berikutnya.
                previous_end = min(previous_end, timestamp)
                close_segment()
                current_words = []
                segment_start = None

        if not current_words:
            segment_start = timestamp

        current_words.append(item["text"])
        previous_time = timestamp
        # Cue bisa bertahan di layar saat jeda; batasi panjang satu kata.
        previous_end = min(item["end"], timestamp + 1.5)

    if current_words:
        close_segment()

    return segments


def _parse_vtt(content):
    return _segments_from_words(_parse_vtt_words(content))


def transcript_from_vtt(content):
    """Ubah isi VTT menjadi segmen dan kata bertimestamp."""
    words = _parse_vtt_words(content)

    return {
        "segments": _segments_from_words(words),
        "words": [
            {
                "start": round(item["time"], 2),
                "end": round(
                    max(item["time"], min(item["end"], item["time"] + 1.5)),
                    2
                ),
                "text": item["text"]
            }
            for item in words
        ]
    }


MAX_SUBTITLE_ATTEMPTS = 6
_ANSI_CODES = re.compile(r"\x1b\[[0-9;]*m")


def _listed_languages(info):
    """Bahasa subtitle yang dilaporkan YouTube: (buatan pembuat, otomatis).

    Mengembalikan (None, None) bila daftar itu tidak tersedia sama sekali.
    """
    manual = info.get("subtitles")
    automatic = info.get("automatic_captions")

    if manual is None and automatic is None:
        return None, None

    return (
        [key for key in (manual or {}) if key != "live_chat"],
        list(automatic or {})
    )


def _subtitle_candidates(info, preferred):
    """Urutan bahasa subtitle yang dicoba.

    1. Bahasa yang diinginkan (Indonesia, lalu Inggris), termasuk varian
       wilayah seperti en-US.
    2. Subtitle otomatis dalam bahasa asli video (kunci berakhiran -orig),
       apa pun bahasanya. Teks ucapan asli tetap bisa dianalisis, terutama
       oleh Claude atau ChatGPT.
    3. Subtitle buatan pembuat video dalam bahasa lain.

    Hanya bahasa yang benar-benar dilaporkan YouTube yang dicoba, supaya
    video tanpa subtitle gagal dengan cepat dan dengan pesan yang benar.
    """
    manual, automatic = _listed_languages(info)

    if manual is None:
        # Daftar tidak tersedia: coba bahasa yang diinginkan apa adanya.
        return list(preferred)

    candidates = []

    def add(key):
        if key not in candidates:
            candidates.append(key)

    for lang in preferred:
        if lang in manual or lang in automatic:
            add(lang)
        for key in manual:
            if key.startswith(lang + "-"):
                add(key)

    for key in automatic:
        if key.endswith("-orig"):
            add(key)

    for key in manual:
        add(key)

    return candidates[:MAX_SUBTITLE_ATTEMPTS]


def _short_error(error):
    text = _ANSI_CODES.sub("", str(error))
    text = re.sub(r"\s+", " ", text).replace("ERROR: ", "").strip()
    return text[:300]


def _no_subtitle_message(info, errors):
    """Pesan gagal yang menyebut penyebab sebenarnya."""
    manual, automatic = _listed_languages(info)
    listed = (manual or []) + [
        key for key in (automatic or []) if key.endswith("-orig")
    ]

    if manual is not None and not manual and not automatic:
        return (
            "YouTube tidak menyediakan subtitle untuk video ini, termasuk "
            "subtitle otomatis. ZOI membaca isi video dari subtitle, jadi "
            "video ini belum bisa dianalisis. Biasanya ini terjadi pada "
            "video yang baru diunggah, video musik, atau video yang "
            "subtitlenya dimatikan pembuatnya."
        )

    if not errors:
        return (
            "Video ini tidak memiliki subtitle yang bisa dipakai. "
            "Coba video lain yang memiliki subtitle/CC."
        )

    tried = ", ".join(lang for lang, _ in errors)
    detail = errors[-1][1]
    message = (
        "Subtitle video ini ada di YouTube tetapi gagal diambil "
        f"(bahasa yang dicoba: {tried}). "
    )

    if "429" in detail or "Too Many Requests" in detail:
        message += (
            "YouTube sedang membatasi permintaan dari jaringan ini. "
            "Tunggu beberapa menit, lalu coba lagi. "
        )

    if listed:
        message += f"Bahasa yang tersedia: {', '.join(listed[:8])}. "

    return message + f"Pesan dari YouTube: {detail}"


def transcribe_from_youtube(
    video_url: str,
    language: str = "id"
) -> Dict:

    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)

    base_options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
    }

    try:
        with YoutubeDL(base_options) as ydl:
            info = ydl.extract_info(
                video_url,
                download=False
            )
    except DownloadError as error:
        raise RuntimeError(
            f"Gagal membaca video YouTube: {_short_error(error)}"
        ) from error

    video_id = info.get("id", "")

    if not video_id:
        raise RuntimeError("ID video YouTube tidak ditemukan.")

    preferred = list(dict.fromkeys([language, "en"]))

    parsed = None
    selected_language = None
    errors = []

    for lang in _subtitle_candidates(info, preferred):
        options = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang],
            "subtitlesformat": "vtt/best",
            "outtmpl": os.path.join(
                LOCAL_OUTPUT_DIR,
                f"youtube_transcript_{video_id}.%(ext)s"
            ),
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
        }

        def subtitle_files():
            return [
                file for file in Path(LOCAL_OUTPUT_DIR).glob(
                    f"youtube_transcript_{video_id}*.{lang}.vtt"
                )
                if file.stat().st_size > 0
            ]

        # Pakai ulang subtitle yang sudah pernah diunduh.
        files = subtitle_files()

        if not files:
            try:
                with YoutubeDL(options) as ydl:
                    ydl.extract_info(video_url, download=True)
            except DownloadError as error:
                errors.append((lang, _short_error(error)))
                continue

            files = subtitle_files()

        if not files:
            errors.append((lang, "YouTube tidak mengirim file subtitle."))
            continue

        subtitle_file = max(
            files,
            key=lambda file: file.stat().st_mtime
        )
        candidate = transcript_from_vtt(
            subtitle_file.read_text(encoding="utf-8", errors="replace")
        )

        if not candidate["segments"]:
            errors.append((lang, "File subtitle tidak berisi teks."))
            continue

        parsed = candidate
        selected_language = lang
        break

    if parsed is None:
        raise RuntimeError(_no_subtitle_message(info, errors))

    segments = parsed["segments"]

    return {
        "title": info.get("title", ""),
        "video_id": video_id,
        "url": info.get("webpage_url") or video_url,
        "channel": info.get("channel") or info.get("uploader") or "",
        # Ukuran video, untuk menentukan kualitas sumber yang dibutuhkan.
        "width": info.get("width"),
        "height": info.get("height"),
        "duration": float(
            info.get("duration")
            or max(s["end"] for s in segments)
        ),
        # "id-orig" adalah subtitle otomatis bahasa asli; bahasanya "id".
        "language": selected_language[:-5]
        if selected_language.endswith("-orig") else selected_language,
        "subtitle_track": selected_language,
        "parser_version": PARSER_VERSION,
        "segments": segments,
        "words": parsed["words"]
    }

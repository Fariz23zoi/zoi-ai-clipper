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


def _parse_vtt(content):
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

    # Gabungkan kata menjadi segmen percakapan.
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
            f"Gagal membaca video YouTube: {str(error)[:300]}"
        ) from error

    video_id = info.get("id", "")

    if not video_id:
        raise RuntimeError("ID video YouTube tidak ditemukan.")

    languages = list(dict.fromkeys([language, "en"]))

    subtitle_file = None
    selected_language = None

    for lang in languages:
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

        try:
            with YoutubeDL(options) as ydl:
                ydl.extract_info(video_url, download=True)
        except DownloadError:
            continue

        files = list(
            Path(LOCAL_OUTPUT_DIR).glob(
                f"youtube_transcript_{video_id}*.{lang}.vtt"
            )
        )

        if files:
            subtitle_file = max(
                files,
                key=lambda file: file.stat().st_mtime
            )
            selected_language = lang
            break

    if subtitle_file is None:
        raise RuntimeError(
            "Subtitle bahasa Indonesia dan Inggris tidak ditemukan."
        )

    content = subtitle_file.read_text(
        encoding="utf-8",
        errors="replace"
    )

    segments = _parse_vtt(content)

    if not segments:
        raise RuntimeError(
            "Subtitle berhasil diunduh, tetapi teks tidak ditemukan."
        )

    return {
        "title": info.get("title", ""),
        "video_id": video_id,
        "duration": float(
            info.get("duration")
            or max(s["end"] for s in segments)
        ),
        "language": selected_language,
        "segments": segments
    }

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


def _parse_vtt(content):
    words = []
    blocks = re.split(r"\n\s*\n", content)

    cue_pattern = re.compile(
        r"^\s*(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->"
    )
    word_pattern = re.compile(
        r"<(00:\d{2}:\d{2}\.\d{3})><c[^>]*>(.*?)</c>",
        re.DOTALL
    )

    first_timed_cue = True

    for block in blocks:
        lines = block.strip().splitlines()

        cue_index = next(
            (i for i, line in enumerate(lines) if "-->" in line),
            None
        )
        if cue_index is None:
            continue

        cue_match = cue_pattern.match(lines[cue_index])
        if not cue_match:
            continue

        cue_start = _to_seconds(cue_match.group(1))
        body = "\n".join(lines[cue_index + 1:])

        matches = list(word_pattern.finditer(body))

        if matches:
            # Hanya ambil teks sebelum timestamp pada cue pertama.
            # Cue berikutnya berisi rolling caption lama.
            if first_timed_cue:
                prefix = _clean_text(body[:matches[0].start()])
                for word in prefix.split():
                    words.append({
                        "time": cue_start,
                        "text": word
                    })
                first_timed_cue = False

            for match in matches:
                text = _clean_text(match.group(2))
                for word in text.split():
                    words.append({
                        "time": _to_seconds(match.group(1)),
                        "text": word
                    })

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

    for item in unique_words:
        timestamp = item["time"]

        if current_words:
            gap = timestamp - previous_time
            duration = timestamp - segment_start

            if gap > 2.0 or duration >= 8.0:
                segments.append({
                    "start": round(segment_start, 2),
                    "end": round(previous_time, 2),
                    "text": " ".join(current_words)
                })
                current_words = []
                segment_start = None

        if not current_words:
            segment_start = timestamp

        current_words.append(item["text"])
        previous_time = timestamp

    if current_words:
        segments.append({
            "start": round(segment_start, 2),
            "end": round(previous_time, 2),
            "text": " ".join(current_words)
        })

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

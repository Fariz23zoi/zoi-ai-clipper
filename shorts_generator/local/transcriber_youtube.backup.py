import os
import re
import html
from pathlib import Path
from typing import Dict

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from ..config import LOCAL_OUTPUT_DIR


def _remove_rolling_duplicates(segments):
    cleaned = []
    history = []

    def normalize(word):
        return re.sub(r"[^a-z0-9]", "", word.lower())

    for segment in segments:
        words = segment["text"].split()
        normalized = [normalize(word) for word in words]

        overlap = 0
        max_size = min(len(words), len(history))

        for size in range(max_size, 2, -1):
            prefix = normalized[:size]

            recent = history[-80:]
            found = any(
                recent[i:i + size] == prefix
                for i in range(len(recent) - size + 1)
            )

            if found:
                overlap = size
                break

        remaining = words[overlap:]

        if remaining:
            cleaned.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": " ".join(remaining)
            })

            history.extend(normalize(word) for word in remaining)
            history = history[-120:]

    return cleaned


def transcribe_from_youtube(video_url: str, language: str = "id") -> Dict:
    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)

    base_options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
    }

    try:
        with YoutubeDL(base_options) as ydl:
            info = ydl.extract_info(video_url, download=False)
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
            "Subtitle bahasa Indonesia dan Inggris tidak ditemukan. "
            "Video mungkin tidak menyediakan subtitle yang bisa diunduh."
        )

    content = subtitle_file.read_text(
        encoding="utf-8",
        errors="replace"
    )

    segments = []

    def to_seconds(value):
        value = value.strip().split(" ")[0]
        parts = value.replace(",", ".").split(":")
        return sum(
            float(part) * (60 ** index)
            for index, part in enumerate(reversed(parts))
        )

    for block in re.split(r"\n\s*\n", content):
        lines = block.strip().splitlines()

        timestamp_index = next(
            (i for i, line in enumerate(lines) if "-->" in line),
            None
        )

        if timestamp_index is None:
            continue

        start_raw, end_raw = lines[timestamp_index].split("-->", 1)

        start = to_seconds(start_raw)
        end = to_seconds(end_raw)

        text_lines = []

        for line in lines[timestamp_index + 1:]:
            line = re.sub(r"<[^>]+>", "", line)
            line = html.unescape(line).strip()

            if not line:
                continue

            if line.startswith("NOTE"):
                continue

            if re.fullmatch(r"[\[♪♫\]\s]+", line):
                continue

            if re.fullmatch(r"[♪♫\s]+", line):
                continue

            text_lines.append(line)

        text = " ".join(text_lines).strip()

        if text:
            segments.append({
                "start": start,
                "end": end,
                "text": text
            })

    segments = _remove_rolling_duplicates(segments)

    if not segments:
        raise RuntimeError(
            "Subtitle berhasil diunduh, tetapi tidak ada teks percakapan."
        )

    return {
        "title": info.get("title", ""),
        "video_id": video_id,
        "duration": float(info.get("duration") or max(
            segment["end"] for segment in segments
        )),
        "language": selected_language,
        "segments": segments
    }

"""Alat bantu bersama untuk tes."""
import shutil
import subprocess
from pathlib import Path

from shorts_generator.local.transcriber_youtube import (
    PARSER_VERSION, transcript_from_vtt,
)

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_ID = "7btFNGS3ATE"


def sample_transcript():
    """Transkrip video contoh (27 menit, subtitle otomatis Indonesia)."""
    parsed = transcript_from_vtt(
        (FIXTURES / "auto_id.vtt").read_text(encoding="utf-8")
    )
    return {
        "title": "Video contoh",
        "video_id": VIDEO_ID,
        "url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
        "channel": "Contoh",
        "duration": 1624.0,
        "language": "id",
        "parser_version": PARSER_VERSION,
        "segments": parsed["segments"],
        "words": parsed["words"],
    }


def ffmpeg_available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_test_video(path, seconds=40, size="1280x720"):
    """Buat video uji dengan gambar dan suara."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        check=True,
    )
    return str(path)


def probe(path):
    """Kembalikan {jenis stream: {codec, width, height, duration}}."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    streams = {}
    for line in out.strip().splitlines():
        parts = line.split(",")
        if parts[1] == "video":
            streams["video"] = {
                "codec": parts[0], "width": int(parts[2]),
                "height": int(parts[3]), "duration": float(parts[4]),
            }
        elif parts[1] == "audio":
            streams["audio"] = {"codec": parts[0], "duration": float(parts[-1])}
    return streams

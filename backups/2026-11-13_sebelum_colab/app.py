"""Dashboard ZOI AI CLIPPER.

Alur tiga halaman:
  1. Tempel link YouTube dan pilih mode.
  2. (Mode Claude/ChatGPT/Hybrid) Salin prompt, tempel jawaban JSON.
     Mode ZOI melewati halaman ini.
  3. Pilih kandidat dan kualitas render. Video diunduh saat render
     pertama, lalu dirender 9:16 dengan progres nyata dari yt-dlp dan
     FFmpeg.

Jalankan:  python app.py
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)

from shorts_generator import exchange, zoi_engine_v4_2
from shorts_generator.ai_analyzer import analyze_with_ai
from shorts_generator.config import (
    CLAUDE_API_KEY, LOCAL_OUTPUT_DIR, OPENAI_API_KEY,
)
from shorts_generator.local.clipper import RENDER_SIZES, crop_clip_local
from shorts_generator.local import speech_to_text
from shorts_generator.local.downloader import (
    _extract_youtube_video_id, download_audio_local, download_youtube_local,
)
from shorts_generator.local.transcriber_youtube import (
    PARSER_VERSION, SubtitlesUnavailable, transcribe_from_youtube,
)
from shorts_generator.zoi_engine import analyze_transcript

# Konsol Windows tidak selalu bisa mencetak semua karakter (misalnya tanda
# panah di log unduhan). Karakter seperti itu diganti, bukan menghentikan
# unduhan atau render.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

OUTPUT_DIR = LOCAL_OUTPUT_DIR
os.makedirs(OUTPUT_DIR, exist_ok=True)

MODES = ("zoi", "claude", "chatgpt", "hybrid")
NUM_CLIPS = 5
MIN_RENDER_SECONDS = 5.0
MAX_RENDER_SECONDS = 180.0
JOB_TTL_SECONDS = 3600

# Pilihan kualitas render: label -> (lebar, tinggi) kanvas 9:16.
QUALITIES = RENDER_SIZES
DEFAULT_QUALITY = os.getenv("ZOI_RENDER_QUALITY", "720").strip().rstrip("pP")
if DEFAULT_QUALITY not in QUALITIES:
    DEFAULT_QUALITY = "720"

# Tingkat kualitas video YouTube, diukur dari sisi terpendeknya (piksel).
SOURCE_TIERS = (144, 240, 360, 480, 720, 1080)
CANVAS_RATIO = 9 / 16
DEFAULT_ASPECT = 16 / 9

# Bawaan: video baru diunduh saat pengguna merender, bukan saat analisis,
# sehingga HP tidak mengunduh video yang akhirnya tidak dipakai.
DOWNLOAD_AT_ANALYSIS = os.getenv(
    "ZOI_DOWNLOAD_AT_ANALYSIS", ""
).strip().lower() in ("1", "true", "yes", "ya")

SOURCE_EXTENSIONS = (".mp4", ".mkv", ".webm")

# Bila YouTube tidak menyediakan subtitle, ucapan dikenali dari suara video
# (Whisper). Isi 0 untuk mematikan.
SPEECH_ENABLED = os.getenv("ZOI_SPEECH", "1").strip().lower() not in (
    "0", "false", "no", "tidak"
)
SPEECH_INSTALL_HINT = (
    "Untuk video seperti ini ZOI bisa mengenali ucapan dari suara videonya, "
    "tetapi pengenal suara (Whisper) belum terpasang. Di laptop, tutup "
    "jendela ZOI lalu jalankan lagi mulai_windows.bat atau mulai.sh; "
    "keduanya memasangnya otomatis. Fitur ini tidak tersedia di HP (Termux)."
)

ZOI_V41 = "ZOI ENGINE V4.1"
ZOI_V42 = "ZOI ENGINE V4.2 (eksperimental)"

# Analis eksternal: hasilnya masuk lewat salin-tempel JSON, tanpa API key.
ANALYSTS = {
    "claude": {
        "name": "Claude", "label": "CLAUDE (IMPOR)",
        "url": "https://claude.ai/new"
    },
    "chatgpt": {
        "name": "ChatGPT", "label": "CHATGPT (IMPOR)",
        "url": "https://chatgpt.com/"
    },
}
MODE_ANALYSTS = {
    "zoi": ("claude", "chatgpt"),
    "claude": ("claude",),
    "chatgpt": ("chatgpt",),
    "hybrid": ("claude", "chatgpt"),
}

RENDER_JOBS = {}
ANALYZE_JOBS = {}
_JOBS_LOCK = threading.Lock()
_ANALYSIS_LOCK = threading.Lock()
# Satu render pada satu waktu: FFmpeg sudah memakai semua inti CPU HP.
_RENDER_SLOT = threading.Semaphore(1)


# ------------------------------------------------------------------ utilitas

@app.template_filter("hms")
def hms(seconds):
    return exchange.format_time(seconds or 0)


def _provider_keys():
    return {"claude": bool(CLAUDE_API_KEY), "chatgpt": bool(OPENAI_API_KEY)}


def _update_job(jobs, job_id, **values):
    with _JOBS_LOCK:
        if job_id in jobs:
            jobs[job_id].update(values)


def _new_job(jobs, **values):
    job_id = uuid.uuid4().hex
    now = time.time()

    with _JOBS_LOCK:
        for old_id in [
            key for key, job in jobs.items()
            if job["status"] in ("done", "error")
            and now - job["created_at"] > JOB_TTL_SECONDS
        ]:
            del jobs[old_id]

        jobs[job_id] = {"status": "running", "error": "", "created_at": now}
        jobs[job_id].update(values)

    return job_id


def _public_job(jobs, job_id):
    with _JOBS_LOCK:
        job = jobs.get(job_id)
        if not job:
            return None
        return {key: value for key, value in job.items() if key != "created_at"}


def _source_path(analysis):
    """Path video sumber di dalam folder output, atau None bila belum ada."""
    names = []

    hinted = os.path.basename(str(analysis.get("source_file") or ""))
    if hinted.startswith("source_"):
        names.append(hinted)

    video_id = str(analysis.get("video_id") or "")
    if exchange.is_valid_video_id(video_id):
        names.extend(f"source_{video_id}{ext}" for ext in SOURCE_EXTENSIONS)

    root = os.path.realpath(OUTPUT_DIR)

    for name in names:
        path = os.path.realpath(os.path.join(OUTPUT_DIR, name))
        if path.startswith(root + os.sep) and os.path.isfile(path):
            return path

    return None


def _size_mb(path):
    try:
        return round(os.path.getsize(path) / 1_048_576, 1)
    except (OSError, TypeError):
        return 0.0


def _source_files(video_id):
    """Semua file video sumber ini, termasuk unduhan yang belum selesai."""
    prefix = f"source_{video_id}."

    try:
        names = os.listdir(OUTPUT_DIR)
    except OSError:
        return []

    return [
        os.path.join(OUTPUT_DIR, name) for name in names
        if name.startswith(prefix)
        and os.path.isfile(os.path.join(OUTPUT_DIR, name))
    ]


def _storage_summary():
    """Ruang yang dipakai video sumber dan hasil render, dalam MB."""
    sources = renders = 0

    try:
        names = os.listdir(OUTPUT_DIR)
    except OSError:
        names = []

    for name in names:
        path = os.path.join(OUTPUT_DIR, name)
        if not os.path.isfile(path):
            continue

        try:
            size = os.path.getsize(path)
        except OSError:
            continue

        if name.startswith("source_"):
            sources += size
        elif _is_render_file(name):
            renders += size

    return {
        "sources_mb": round(sources / 1_048_576, 1),
        "renders_mb": round(renders / 1_048_576, 1)
    }


def _has_active_render(video_id):
    with _JOBS_LOCK:
        return any(
            job.get("video_id") == video_id
            and job["status"] in ("queued", "running")
            for job in RENDER_JOBS.values()
        )


def _update_analysis(video_id, change):
    """Ubah analysis.json tanpa menimpa perubahan dari proses lain."""
    with _ANALYSIS_LOCK:
        analysis = exchange.load_analysis(video_id)
        if analysis is None:
            return None

        change(analysis)
        exchange.save_analysis(video_id, analysis)
        return analysis


# ----------------------------------------------------------- kualitas sumber

def _aspect(analysis):
    """Rasio lebar/tinggi video sumber; 16:9 bila tidak diketahui."""
    try:
        value = float(analysis.get("aspect") or 0)
    except (TypeError, ValueError):
        value = 0.0

    return value if 0.2 <= value <= 5 else DEFAULT_ASPECT


def _needed_source_res(canvas_width, aspect):
    """Kualitas sumber paling ringan yang masih mengisi kanvas tanpa
    diperbesar. Nilainya salah satu SOURCE_TIERS (sisi terpendek video).

    Pada kanvas 9:16, video mendatar dipasang selebar kanvas, jadi yang
    menentukan adalah lebar video, bukan tingginya. Contoh: kanvas 720
    piksel hanya butuh sumber 480p, karena video 480p lebarnya 854 piksel.
    """
    if aspect >= 1:
        needed = canvas_width / aspect
    elif aspect >= CANVAS_RATIO:
        needed = canvas_width
    else:
        needed = canvas_width / CANVAS_RATIO * aspect

    for tier in SOURCE_TIERS:
        if tier >= needed - 1:
            return tier

    return SOURCE_TIERS[-1]


def _probe_short_side(path):
    """Sisi terpendek video (piksel) menurut ffprobe, atau None."""
    try:
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True, timeout=60
        ).stdout
        width, height = (int(value) for value in output.strip().split(",")[:2])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

    return min(width, height) if min(width, height) > 0 else None


def _source_res(analysis, source_path):
    """Kualitas video sumber yang tersimpan; 0 bila belum ada file."""
    if not source_path:
        return 0

    try:
        recorded = int(analysis.get("source_res") or 0)
    except (TypeError, ValueError):
        recorded = 0

    if recorded > 0:
        return recorded

    # File dari versi lama tidak punya catatan: baca ukurannya. Bila tidak
    # terbaca, anggap cukup supaya tidak mengunduh ulang tanpa alasan.
    return _probe_short_side(source_path) or 10 ** 6


def _quality_options(analysis, source_path):
    """Daftar pilihan kualitas beserta sumber yang dibutuhkan masing-masing."""
    have = _source_res(analysis, source_path)
    aspect = _aspect(analysis)
    options = []

    for quality, (width, height) in QUALITIES.items():
        needed = _needed_source_res(width, aspect)
        options.append({
            "id": quality,
            "size": f"{width}x{height}",
            "source": needed,
            "ready": bool(source_path) and have >= needed
        })

    return options


def _progress_hook(report):
    """Hook yt-dlp yang meneruskan kemajuan unduhan ke `report`."""
    def hook(status):
        state = status.get("status")

        if state == "finished":
            report("Menyelesaikan unduhan", None)
            return

        if state != "downloading":
            return

        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes") or 0
        is_audio = (status.get("info_dict") or {}).get("vcodec") == "none"
        label = "Mengunduh audio" if is_audio else "Mengunduh video"

        if total:
            report(label, max(0, min(99, int(done * 100 / total))))
        else:
            report(f"{label} ({done / 1_048_576:.0f} MB)", None)

    return hook


def _download_source(video_url, tier, report):
    """Unduh video sumber; `report(tahap, persen)` menerima progres yt-dlp."""
    report("Menghubungi YouTube", None)
    path = os.path.realpath(download_youtube_local(
        video_url, fmt=str(tier), out_dir=OUTPUT_DIR,
        progress_hook=_progress_hook(report)
    ))
    root = os.path.realpath(OUTPUT_DIR)

    if not (path.startswith(root + os.sep) and os.path.isfile(path)):
        raise RuntimeError("Unduhan selesai tetapi file video tidak ditemukan.")

    return path


def _fetch_source(video_id, video_url, tier, existing_path, report):
    """Unduh sumber pada kualitas `tier` dan catat di analysis.json.

    Bila sudah ada file dengan kualitas lebih rendah, file itu disisihkan
    dulu dan baru dihapus setelah unduhan baru berhasil. Bila unduhan
    gagal, file lama dikembalikan.
    """
    backup = None

    if existing_path:
        backup = existing_path + ".lama"
        os.replace(existing_path, backup)

    try:
        path = _download_source(video_url, tier, report)
    except Exception:
        if backup and os.path.exists(backup):
            os.replace(backup, existing_path)
        raise

    if backup and os.path.exists(backup):
        os.remove(backup)

    name = os.path.basename(path)
    _update_analysis(
        video_id,
        lambda current: current.update(source_file=name, source_res=tier)
    )
    return path


# ------------------------------------------------------------- hasil render

def _is_render_file(filename):
    return (
        filename == os.path.basename(filename)
        and filename.startswith("zoi_")
        and filename.endswith(".mp4")
        and ".part." not in filename
        and ".cut." not in filename
    )


def _list_renders(video_id=None, limit=None):
    """Daftar hasil render, terbaru lebih dulu."""
    prefix = f"zoi_{video_id}_" if video_id else "zoi_"
    items = []

    try:
        names = os.listdir(OUTPUT_DIR)
    except OSError:
        names = []

    for name in names:
        if not _is_render_file(name) or not name.startswith(prefix):
            continue

        path = os.path.join(OUTPUT_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue

        items.append({
            "filename": name,
            "size_mb": round(stat.st_size / 1_048_576, 1),
            "modified": time.strftime(
                "%d %b %Y %H:%M", time.localtime(stat.st_mtime)
            ),
            "mtime": stat.st_mtime
        })

    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items[:limit] if limit else items


def _clip_from_highlight(item):
    return {
        "title": str(item.get("title", "") or "Kandidat")[:100],
        "start_time": float(item["start_time"]),
        "end_time": float(item["end_time"]),
        "duration": round(float(item["end_time"]) - float(item["start_time"]), 2),
        "score": item.get("score", 0),
        "hook_sentence": str(item.get("hook_sentence", "") or ""),
        "virality_reason": str(item.get("virality_reason", "") or ""),
        "warnings": list(item.get("warnings", []))
    }


def _mark_agreement(groups):
    """Tandai kandidat AI yang juga dipilih mesin ZOI."""
    zoi_clips = [
        clip for group in groups if group.get("kind") == "zoi"
        for clip in group["clips"]
    ]

    for group in groups:
        for clip in group["clips"]:
            clip["agrees_with_zoi"] = group.get("kind") != "zoi" and any(
                exchange.overlap_ratio(clip, other) >= exchange.MAX_OVERLAP
                for other in zoi_clips
            )


def _analysts_for(mode):
    """Analis eksternal yang relevan untuk sebuah mode."""
    return [
        dict(id=key, **ANALYSTS[key])
        for key in MODE_ANALYSTS.get(mode, MODE_ANALYSTS["zoi"])
    ]


def _home(error="", status=200):
    videos = exchange.list_videos()[:10]

    for item in videos:
        item["source_mb"] = _size_mb(_source_path(item))

    return render_template(
        "home.html",
        error=error,
        keys=_provider_keys(),
        videos=videos,
        renders=_list_renders(limit=10),
        storage=_storage_summary()
    ), status


# ------------------------------------------------------------------ analisis

def _run_zoi_engines(transcript):
    groups = []

    for name, engine in (
        (ZOI_V41, analyze_transcript),
        (ZOI_V42, zoi_engine_v4_2.analyze_transcript),
    ):
        highlights = engine(transcript, NUM_CLIPS).get("highlights", [])
        groups.append({
            "name": name,
            "kind": "zoi",
            "clips": [_clip_from_highlight(item) for item in highlights]
        })

    return groups


def _run_api_providers(transcript, mode, notices):
    """Jalur API lama. Hanya dipakai bila API key memang diatur."""
    keys = _provider_keys()
    labels = {"claude": "CLAUDE AI", "chatgpt": "CHATGPT AI"}
    groups = []

    for provider in MODE_ANALYSTS.get(mode, ()):
        if not keys[provider]:
            continue

        try:
            result = analyze_with_ai(transcript, provider, NUM_CLIPS)
        except Exception as error:  # jaringan, kuota, format jawaban
            notices.append(
                f"Analisis {labels[provider]} lewat API gagal: "
                f"{str(error)[:200]}"
            )
            continue

        groups.append({
            "name": labels[provider],
            "kind": "api",
            "clips": [
                _clip_from_highlight(item)
                for item in result.get("highlights", [])
            ]
        })

    return groups


def _transcript_aspect(transcript):
    try:
        width = int(transcript.get("width") or 0)
        height = int(transcript.get("height") or 0)
    except (TypeError, ValueError):
        return None

    return round(width / height, 4) if width > 0 and height > 0 else None


def _audio_files(video_id):
    prefix = f"audio_{video_id}."
    try:
        names = os.listdir(OUTPUT_DIR)
    except OSError:
        return []
    return [
        os.path.join(OUTPUT_DIR, name) for name in names
        if name.startswith(prefix)
    ]


def _transcribe_speech(video_url, error, stage):
    """Buat transkrip dari suara video bila subtitle tidak bisa dipakai.

    Memakai video sumber bila sudah ada di HP/laptop; selain itu hanya
    audionya yang diunduh, lalu dihapus setelah transkrip jadi.
    """
    video = error.video
    video_id = str(video.get("video_id") or "")

    if not SPEECH_ENABLED:
        raise RuntimeError(str(error))

    if not speech_to_text.is_available():
        raise RuntimeError(f"{error} {SPEECH_INSTALL_HINT}")

    if not exchange.is_valid_video_id(video_id):
        raise RuntimeError("ID video dari YouTube tidak valid.")

    if error.reason == "none":
        stage("Video ini tidak punya subtitle. Transkrip dibuat dari suara", None)
    else:
        stage("Subtitle gagal diambil. Transkrip dibuat dari suara", None)

    media = _source_path({"video_id": video_id})

    try:
        if not media:
            stage("Mengunduh audio", None)
            try:
                media = download_audio_local(
                    video_url, out_dir=OUTPUT_DIR,
                    progress_hook=_progress_hook(stage)
                )
            except Exception as download_error:
                raise RuntimeError(
                    f"{error} Audio untuk pengenal suara juga gagal "
                    f"diunduh: {str(download_error)[:300]}"
                ) from download_error

        result = speech_to_text.transcribe_file(
            media, report=stage, language_hint=video.get("language")
        )
    finally:
        for path in _audio_files(video_id):
            try:
                os.remove(path)
            except OSError:
                pass

    if not result["segments"]:
        raise RuntimeError(
            "Pengenal suara tidak menemukan ucapan di video ini. Mungkin "
            "isinya hanya musik atau suara tanpa kata-kata."
        )

    return {
        "title": video.get("title", ""),
        "video_id": video_id,
        "url": video.get("url") or video_url,
        "channel": video.get("channel", ""),
        "width": video.get("width"),
        "height": video.get("height"),
        "duration": float(
            video.get("duration") or result.get("duration")
            or max(segment["end"] for segment in result["segments"])
        ),
        "language": result["language"],
        "source": "whisper",
        "speech_model": result.get("model", ""),
        "speech_reason": error.reason,
        "parser_version": PARSER_VERSION,
        "segments": result["segments"],
        "words": result["words"]
    }


def _analyze_worker(job_id, video_url, mode):
    def stage(text, progress=None):
        _update_job(ANALYZE_JOBS, job_id, stage=text, progress=progress)

    try:
        stage("Mengambil subtitle YouTube")

        guessed_id = _extract_youtube_video_id(video_url)
        transcript = None

        if guessed_id and exchange.is_valid_video_id(guessed_id):
            cached = exchange.load_transcript(guessed_id)
            if cached and cached.get("parser_version") == PARSER_VERSION:
                transcript = cached

        if transcript is None:
            try:
                transcript = transcribe_from_youtube(video_url)
            except SubtitlesUnavailable as error:
                transcript = _transcribe_speech(video_url, error, stage)
            if not exchange.is_valid_video_id(transcript.get("video_id")):
                raise RuntimeError("ID video dari YouTube tidak valid.")
            exchange.save_transcript(transcript)

        video_id = transcript["video_id"]
        aspect = _transcript_aspect(transcript)

        stage("Menganalisis transkrip dengan ZOI Engine")
        notices = []
        groups = _run_zoi_engines(transcript)

        from_speech = transcript.get("source") == "whisper"
        if from_speech:
            why = (
                "tidak menyediakan subtitle"
                if transcript.get("speech_reason") == "none"
                else "gagal mengirim subtitle"
            )
            notices.append(
                f"YouTube {why} untuk video ini, jadi transkrip dibuat dari "
                "suara video dengan pengenal suara (Whisper "
                f"{transcript.get('speech_model') or ''}). Bisa ada kata yang "
                "salah dengar; tonton klipnya sebelum diunggah."
            )

        language = str(transcript.get("language") or "")
        if language and not language.lower().startswith("id"):
            notices.append(
                f"{'Ucapan' if from_speech else 'Subtitle'} video ini "
                f"berbahasa \"{language}\", bukan "
                "Indonesia. ZOI Engine memakai kata kunci bahasa Indonesia, "
                "jadi kandidatnya bisa sedikit atau kurang tepat. Analisis "
                "Claude atau ChatGPT tetap bisa dipakai."
            )

        if mode != "zoi":
            stage("Menjalankan analisis AI")
            groups.extend(_run_api_providers(transcript, mode, notices))

        downloaded = {}
        if DOWNLOAD_AT_ANALYSIS and not _source_path({"video_id": video_id}):
            tier = _needed_source_res(
                QUALITIES[DEFAULT_QUALITY][0], aspect or DEFAULT_ASPECT
            )
            path = _download_source(video_url, tier, stage)
            downloaded = {
                "source_file": os.path.basename(path), "source_res": tier
            }

        with _ANALYSIS_LOCK:
            previous = exchange.load_analysis(video_id) or {}
            groups.extend(
                group for group in previous.get("groups", [])
                if group.get("kind") == "import"
            )

            if not any(group["clips"] for group in groups):
                notices.append(
                    "ZOI Engine tidak menemukan kandidat pada video ini. "
                    "Anda masih bisa memakai analisis Claude atau ChatGPT."
                )

            exchange.save_analysis(video_id, {
                "video_id": video_id,
                "title": transcript.get("title") or video_id,
                "url": transcript.get("url") or video_url,
                "duration": exchange.transcript_duration(transcript),
                "language": transcript.get("language", ""),
                "mode": mode,
                "aspect": aspect or previous.get("aspect"),
                "source_file": downloaded.get(
                    "source_file", previous.get("source_file", "")
                ),
                "source_res": downloaded.get(
                    "source_res", previous.get("source_res", 0)
                ),
                "updated_at": time.time(),
                "notices": notices,
                "groups": groups
            })

        # Mode AI tanpa API key berlanjut ke halaman ekspor/impor.
        keys = _provider_keys()
        needs_import = mode != "zoi" and not all(
            keys[provider] for provider in MODE_ANALYSTS[mode]
        )

        _update_job(
            ANALYZE_JOBS, job_id,
            status="done", stage="Analisis selesai", progress=100,
            video_id=video_id, next="ai" if needs_import else "video"
        )

    except Exception as error:
        _update_job(
            ANALYZE_JOBS, job_id,
            status="error", stage="Analisis gagal",
            error=f"Gagal menganalisis video: {str(error)[:600]}"
        )


# ---------------------------------------------------------------------- rute

@app.route("/")
def home():
    return _home()


@app.route("/analyze", methods=["POST"])
def analyze():
    video_url = request.form.get("youtube_url", "").strip()
    mode = request.form.get("mode", "zoi")

    if mode not in MODES:
        return _home("Mode analisis tidak dikenal.", 400)

    if not re.match(r"^https?://\S+$", video_url):
        return _home(
            "Masukkan link YouTube yang diawali http:// atau https://.", 400
        )

    job_id = _new_job(
        ANALYZE_JOBS, stage="Menyiapkan", progress=None, video_id="", next=""
    )
    threading.Thread(
        target=_analyze_worker, args=(job_id, video_url, mode), daemon=True
    ).start()

    return render_template(
        "analyzing.html", job_id=job_id, video_url=video_url,
        downloads=DOWNLOAD_AT_ANALYSIS
    )


@app.route("/analyze/status/<job_id>")
def analyze_status(job_id):
    job = _public_job(ANALYZE_JOBS, job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)


def _load_video(video_id):
    if not exchange.is_valid_video_id(video_id):
        abort(404)

    analysis = exchange.load_analysis(video_id)
    if not analysis:
        abort(404)

    return analysis


def _load_transcript(video_id):
    transcript = exchange.load_transcript(video_id)
    if not transcript:
        abort(404)
    return transcript


# ------------------------------------------- halaman 3: pilih dan render

def _render_video_page(analysis, status=200, **extra):
    groups = analysis.get("groups", [])
    _mark_agreement(groups)

    # Hasil AI ditampilkan lebih dulu; hasil ZOI menyusul di bawahnya.
    ai_groups = [group for group in groups if group.get("kind") != "zoi"]
    zoi_groups = [group for group in groups if group.get("kind") == "zoi"]

    source_path = _source_path(analysis)
    mode = analysis.get("mode", "zoi")

    return render_template(
        "video.html",
        video=analysis,
        groups=ai_groups + zoi_groups,
        has_ai=bool(ai_groups),
        ai_names=" dan ".join(item["name"] for item in _analysts_for(mode)),
        source_ready=source_path is not None,
        source_mb=_size_mb(source_path),
        can_render=source_path is not None or bool(analysis.get("url")),
        qualities=_quality_options(analysis, source_path),
        default_quality=DEFAULT_QUALITY,
        renders=_list_renders(video_id=analysis["video_id"]),
        **extra
    ), status


@app.route("/video/<video_id>")
def video_page(video_id):
    return _render_video_page(
        _load_video(video_id),
        imported=request.args.get("imported", ""),
        deleted=request.args.get("deleted", "")
    )


# ------------------------------------------ halaman 2: ekspor dan impor

def _render_ai_page(analysis, status=200, **extra):
    analysts = _analysts_for(analysis.get("mode", "zoi"))
    counts = {
        group["name"]: len(group.get("clips", []))
        for group in analysis.get("groups", [])
        if group.get("kind") == "import"
    }

    for analyst in analysts:
        analyst["imported_count"] = counts.get(analyst["label"], 0)

    context = {
        "errors": {}, "texts": {}, "rejected": {}, "imported": [],
        "general_error": ""
    }
    context.update(extra)

    return render_template(
        "ai.html",
        video=analysis,
        analysts=analysts,
        limits=exchange,
        **context
    ), status


@app.route("/video/<video_id>/ai")
def ai_page(video_id):
    return _render_ai_page(_load_video(video_id))


@app.route("/video/<video_id>/export.json")
def export_json(video_id):
    _load_video(video_id)
    body = json.dumps(
        exchange.build_export(_load_transcript(video_id)),
        ensure_ascii=False, indent=1
    )
    return Response(
        body, mimetype="application/json",
        headers={
            "Content-Disposition":
                f'attachment; filename="zoi_export_{video_id}.json"'
        }
    )


@app.route("/video/<video_id>/prompt.txt")
def export_prompt(video_id):
    _load_video(video_id)
    body = exchange.build_prompt(
        exchange.build_export(_load_transcript(video_id)), NUM_CLIPS
    )
    headers = {}

    if request.args.get("dl"):
        headers["Content-Disposition"] = (
            f'attachment; filename="zoi_prompt_{video_id}.txt"'
        )

    return Response(
        body, mimetype="text/plain; charset=utf-8", headers=headers
    )


def _save_import_group(video_id, label, result):
    group = {
        "name": label,
        "kind": "import",
        "imported_at": time.time(),
        "clips": result["accepted"],
        "rejected": result["rejected"],
        "file_warnings": result["file_warnings"]
    }

    def add_group(current):
        current["groups"] = [
            old for old in current.get("groups", [])
            if old.get("name") != label
        ] + [group]
        current["updated_at"] = time.time()

    _update_analysis(video_id, add_group)


@app.route("/video/<video_id>/import", methods=["POST"])
def import_analysis(video_id):
    """Terima jawaban JSON dari tiap analis, validasi, lalu simpan."""
    analysis = _load_video(video_id)
    transcript = _load_transcript(video_id)

    errors, texts, rejected, imported = {}, {}, {}, []
    provided = False

    for key, analyst in ANALYSTS.items():
        raw = request.form.get(f"json_{key}", "")
        upload = request.files.get(f"file_{key}")

        if upload and upload.filename:
            raw = upload.read(exchange.MAX_IMPORT_BYTES + 1).decode(
                "utf-8", errors="replace"
            )

        if not raw.strip():
            continue

        provided = True

        try:
            result = exchange.validate_import(
                exchange.parse_import(raw), transcript
            )
        except exchange.ExchangeError as error:
            errors[key] = str(error)
            texts[key] = raw[:200000]
            continue

        if not result["accepted"]:
            errors[key] = "Tidak ada kandidat yang lolos validasi."
            rejected[key] = result["rejected"]
            texts[key] = raw[:200000]
            continue

        _save_import_group(video_id, analyst["label"], result)
        imported.append(analyst["label"])

    if not provided:
        return _render_ai_page(
            analysis, 400,
            general_error="Tempel jawaban JSON terlebih dahulu."
        )

    if errors:
        # Yang sudah benar tetap tersimpan; yang salah bisa diperbaiki.
        return _render_ai_page(
            exchange.load_analysis(video_id) or analysis, 400,
            errors=errors, texts=texts, rejected=rejected, imported=imported
        )

    return redirect(
        url_for("video_page", video_id=video_id, imported=", ".join(imported))
    )


@app.route("/video/<video_id>/group/delete", methods=["POST"])
def delete_group(video_id):
    _load_video(video_id)
    name = request.form.get("name", "")

    def remove_group(current):
        current["groups"] = [
            group for group in current.get("groups", [])
            if not (group.get("kind") == "import" and group.get("name") == name)
        ]

    _update_analysis(video_id, remove_group)

    return redirect(url_for("video_page", video_id=video_id))


@app.route("/video/<video_id>/source/delete", methods=["POST"])
def delete_source(video_id):
    """Hapus video sumber atas permintaan pengguna untuk mengosongkan ruang.
    Analisis dan kandidat tetap ada; video diunduh lagi saat render."""
    analysis = _load_video(video_id)

    if _has_active_render(video_id):
        return _render_video_page(
            analysis, 409,
            page_error="Video ini sedang dipakai untuk rendering. "
                       "Tunggu sampai selesai, lalu hapus."
        )

    for path in _source_files(video_id):
        try:
            os.remove(path)
        except OSError:
            pass

    _update_analysis(
        video_id,
        lambda current: current.update(source_file="", source_res=0)
    )

    return redirect(url_for("video_page", video_id=video_id, deleted="sumber"))


@app.route("/clip/delete", methods=["POST"])
def delete_clip():
    """Hapus satu hasil render atas permintaan pengguna."""
    filename = request.form.get("filename", "")
    video_id = request.form.get("video_id", "")

    if not _is_render_file(filename):
        abort(404)

    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(path):
        abort(404)

    os.remove(path)

    if exchange.is_valid_video_id(video_id) and exchange.load_analysis(video_id):
        return redirect(url_for("video_page", video_id=video_id, deleted="klip"))
    return redirect(url_for("home"))


@app.route("/select", methods=["POST"])
def select():
    video_id = request.form.get("video_id", "")
    title = request.form.get("title", "")[:100]
    analyst = request.form.get("analyst", "")[:60]

    if not exchange.is_valid_video_id(video_id):
        return "Video tidak dikenal.", 400

    analysis = exchange.load_analysis(video_id)
    if not analysis:
        return "Video belum dianalisis.", 400

    try:
        start = float(request.form.get("start_time", ""))
        end = float(request.form.get("end_time", ""))
    except ValueError:
        return "Timestamp tidak valid.", 400

    duration = float(analysis.get("duration") or 0)

    if not (start == start and end == end) or start < 0 or end <= start:
        return "Timestamp klip tidak valid.", 400

    if duration and end > duration + 1:
        return "Timestamp klip melewati durasi video.", 400

    if not MIN_RENDER_SECONDS <= end - start <= MAX_RENDER_SECONDS:
        return "Durasi klip di luar batas yang diizinkan.", 400

    quality = request.form.get("quality", DEFAULT_QUALITY).strip().rstrip("pP")
    if quality not in QUALITIES:
        return "Kualitas render tidak dikenal.", 400

    size = QUALITIES[quality]
    needed = _needed_source_res(size[0], _aspect(analysis))

    video_url = str(analysis.get("url") or "")
    if not _source_path(analysis) and not video_url:
        return (
            "File video sumber tidak ada dan alamat videonya tidak tersimpan. "
            "Analisis ulang video ini dari halaman utama."
        ), 400

    safe_title = re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_")[:30] or "klip"
    filename = (
        f"zoi_{video_id}_{int(start)}-{int(end)}_{quality}p_{safe_title}_"
        f"{uuid.uuid4().hex[:6]}.mp4"
    )
    output_path = os.path.join(OUTPUT_DIR, filename)

    job_id = _new_job(
        RENDER_JOBS,
        progress=0, stage="Menunggu giliran render", status="queued",
        step="", filename=filename, video_id=video_id, quality=quality
    )

    def render_worker():
        with _RENDER_SLOT:
            try:
                # Diperiksa di sini, bukan saat tombol ditekan: render lain
                # yang lebih dulu antre mungkin sudah mengunduh videonya.
                current = exchange.load_analysis(video_id) or analysis
                source_path = _source_path(current)
                step = ""

                # Unduh bila belum ada, atau bila yang tersimpan kualitasnya
                # terlalu rendah untuk ukuran render yang dipilih. Tanpa
                # alamat video, pakai file yang ada apa adanya.
                if not source_path or (
                    video_url and _source_res(current, source_path) < needed
                ):
                    step = "Langkah 2 dari 2"

                    def report(stage, progress):
                        _update_job(
                            RENDER_JOBS, job_id, stage=stage, progress=progress
                        )

                    _update_job(
                        RENDER_JOBS, job_id,
                        status="running", step="Langkah 1 dari 2",
                        stage="Mengunduh video", progress=None
                    )

                    try:
                        source_path = _fetch_source(
                            video_id, video_url, needed, source_path, report
                        )
                    except Exception as error:
                        raise RuntimeError(
                            f"Video gagal diunduh: {str(error)[:500]}"
                        ) from error

                _update_job(
                    RENDER_JOBS, job_id,
                    status="running", step=step,
                    stage="Menyiapkan rendering", progress=0
                )

                def update_progress(percent, stage):
                    _update_job(
                        RENDER_JOBS, job_id,
                        progress=max(0, min(99, int(percent))), stage=stage
                    )

                crop_clip_local(
                    source_path, start, end, "9:16", output_path,
                    progress_callback=update_progress, size=size
                )

                # 100% hanya setelah file akhir benar-benar ada.
                if not os.path.isfile(output_path):
                    raise RuntimeError("File hasil render tidak ditemukan.")

                _update_job(
                    RENDER_JOBS, job_id,
                    progress=100, stage="Rendering selesai", status="done"
                )

            except Exception as error:
                if os.path.exists(output_path):
                    os.remove(output_path)
                _update_job(
                    RENDER_JOBS, job_id,
                    status="error", stage="Proses gagal",
                    error=str(error)[-800:]
                )

    threading.Thread(target=render_worker, daemon=True).start()

    return render_template(
        "render.html",
        title=title, analyst=analyst, duration=round(end - start, 1),
        start=start, end=end, job_id=job_id, video=analysis,
        quality=quality, size=f"{size[0]}x{size[1]}"
    )


@app.route("/progress/<job_id>")
def render_progress(job_id):
    job = _public_job(RENDER_JOBS, job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)


@app.route("/output/<path:filename>")
def serve_video(filename):
    # Hanya hasil render yang disajikan, bukan video sumber atau data lain.
    if not _is_render_file(filename):
        abort(404)

    return send_from_directory(
        OUTPUT_DIR, filename, as_attachment=bool(request.args.get("dl"))
    )


@app.errorhandler(404)
def not_found(_error):
    return _home("Halaman atau video tidak ditemukan.", 404)


@app.errorhandler(413)
def too_large(_error):
    return "File terlalu besar (batas 2 MB).", 413


# ------------------------------------------------------------- menjalankan

def _lan_address():
    """Alamat perangkat ini di jaringan Wi-Fi/LAN, atau None bila tidak ada.

    Soket UDP hanya dipakai untuk menanyakan rute; tidak ada data terkirim.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
    except OSError:
        return None

    if not address or address.startswith("127.") or address == "0.0.0.0":
        return None
    return address


def _local_url(host, port):
    local = host in ("0.0.0.0", "::", "127.0.0.1", "localhost", "")
    return f"http://{'127.0.0.1' if local else host}:{port}"


def _startup_lines(host, port):
    """Petunjuk yang dicetak saat aplikasi mulai berjalan."""
    lines = [
        "ZOI AI CLIPPER berjalan.",
        f"Buka di browser perangkat ini: {_local_url(host, port)}"
    ]

    if host in ("0.0.0.0", "::"):
        address = _lan_address()
        if address:
            lines.append(
                f"Buka dari HP di Wi-Fi yang sama: http://{address}:{port}"
            )
        else:
            lines.append(
                "Alamat Wi-Fi perangkat ini tidak terbaca. Pastikan sudah "
                "tersambung ke Wi-Fi, lalu jalankan lagi."
            )
        lines.append(
            "Siapa pun di Wi-Fi yang sama bisa membuka dashboard ini."
        )

    lines.append("Untuk berhenti: tekan Ctrl+C atau tutup jendela ini.")
    return lines


def _open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:  # tidak ada browser: cukup buka alamatnya sendiri
        pass


if __name__ == "__main__":
    # Bawaan hanya bisa dibuka dari perangkat ini sendiri. Untuk membuka
    # dari HP di Wi-Fi yang sama: ZOI_HOST=0.0.0.0 (lihat PANDUAN_ZOI.md).
    host = os.getenv("ZOI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("ZOI_PORT", "5000"))

    for line in _startup_lines(host, port):
        print(line, flush=True)

    if os.getenv("ZOI_OPEN_BROWSER", "").strip().lower() in (
        "1", "true", "yes", "ya"
    ):
        timer = threading.Timer(1.5, _open_browser, args=(_local_url(host, port),))
        timer.daemon = True
        timer.start()

    app.run(host=host, port=port, debug=False, threaded=True)

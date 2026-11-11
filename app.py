"""Dashboard ZOI AI CLIPPER.

Alur: input URL -> transkrip -> analisis ZOI -> unduh video ->
(opsional) ekspor/impor analisis AI lewat JSON -> pilih kandidat ->
render 9:16 dengan progres FFmpeg -> putar/unduh hasil.

Jalankan:  python app.py
"""
import json
import os
import re
import threading
import time
import uuid

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)

from shorts_generator import exchange, zoi_engine_v4_2
from shorts_generator.ai_analyzer import analyze_with_ai
from shorts_generator.config import (
    CLAUDE_API_KEY, LOCAL_OUTPUT_DIR, OPENAI_API_KEY,
)
from shorts_generator.local.clipper import crop_clip_local
from shorts_generator.local.downloader import (
    _extract_youtube_video_id, download_youtube_local,
)
from shorts_generator.local.transcriber_youtube import (
    PARSER_VERSION, transcribe_from_youtube,
)
from shorts_generator.zoi_engine import analyze_transcript

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

OUTPUT_DIR = LOCAL_OUTPUT_DIR
os.makedirs(OUTPUT_DIR, exist_ok=True)

MODES = ("zoi", "claude", "chatgpt", "hybrid")
NUM_CLIPS = 5
MIN_RENDER_SECONDS = 5.0
MAX_RENDER_SECONDS = 180.0
JOB_TTL_SECONDS = 3600

# Kualitas video sumber. Hasil akhir selalu 720x1280 dan bagian video di
# tengahnya selebar 720 piksel, jadi sumber 480p (lebar 854 piksel) sudah
# cukup dan jauh lebih ringan untuk diunduh serta diproses di HP.
DOWNLOAD_HEIGHTS = ("480", "720")
DEFAULT_DOWNLOAD_HEIGHT = os.getenv("ZOI_DOWNLOAD_HEIGHT", "480").strip()
if DEFAULT_DOWNLOAD_HEIGHT not in DOWNLOAD_HEIGHTS:
    DEFAULT_DOWNLOAD_HEIGHT = "480"

# Bawaan: video baru diunduh saat pengguna merender, bukan saat analisis,
# sehingga HP tidak mengunduh video yang akhirnya tidak dipakai.
DOWNLOAD_AT_ANALYSIS = os.getenv(
    "ZOI_DOWNLOAD_AT_ANALYSIS", ""
).strip().lower() in ("1", "true", "yes", "ya")

SOURCE_EXTENSIONS = (".mp4", ".mkv", ".webm")

ZOI_V41 = "ZOI ENGINE V4.1"
ZOI_V42 = "ZOI ENGINE V4.2 (eksperimental)"

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


def _download_source(video_url, height, report):
    """Unduh video sumber; `report(tahap, persen)` menerima progres yt-dlp."""
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

    report("Menghubungi YouTube", None)
    path = os.path.realpath(download_youtube_local(
        video_url, fmt=height, out_dir=OUTPUT_DIR, progress_hook=hook
    ))
    root = os.path.realpath(OUTPUT_DIR)

    if not (path.startswith(root + os.sep) and os.path.isfile(path)):
        raise RuntimeError("Unduhan selesai tetapi file video tidak ditemukan.")

    return path


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
    """Tandai kandidat impor yang juga dipilih mesin ZOI."""
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
        storage=_storage_summary(),
        default_height=DEFAULT_DOWNLOAD_HEIGHT
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
    wanted = {
        "claude": ["claude"],
        "chatgpt": ["chatgpt"],
        "hybrid": ["claude", "chatgpt"]
    }.get(mode, [])

    keys = _provider_keys()
    labels = {"claude": "CLAUDE AI", "chatgpt": "CHATGPT AI"}
    groups = []

    for provider in wanted:
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

    if wanted and not any(keys[provider] for provider in wanted):
        notices.append(
            "Mode ini berjalan tanpa API key. Gunakan bagian "
            "\"Analisis AI eksternal\" di bawah: ekspor transkrip, berikan "
            "ke Claude, lalu impor jawaban JSON-nya."
        )

    return groups


def _analyze_worker(job_id, video_url, mode, height):
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
            transcript = transcribe_from_youtube(video_url)
            if not exchange.is_valid_video_id(transcript.get("video_id")):
                raise RuntimeError("ID video dari YouTube tidak valid.")
            exchange.save_transcript(transcript)

        video_id = transcript["video_id"]

        stage("Menganalisis transkrip dengan ZOI Engine")
        notices = []
        groups = _run_zoi_engines(transcript)

        if mode != "zoi":
            stage("Menjalankan analisis AI")
            groups.extend(_run_api_providers(transcript, mode, notices))

        source_file = ""
        if DOWNLOAD_AT_ANALYSIS:
            source_file = os.path.basename(
                _download_source(video_url, height, stage)
            )

        with _ANALYSIS_LOCK:
            previous = exchange.load_analysis(video_id) or {}
            groups.extend(
                group for group in previous.get("groups", [])
                if group.get("kind") == "import"
            )

            if not any(group["clips"] for group in groups):
                notices.append(
                    "ZOI Engine tidak menemukan kandidat pada video ini. "
                    "Anda masih bisa memakai analisis AI eksternal di bawah."
                )

            exchange.save_analysis(video_id, {
                "video_id": video_id,
                "title": transcript.get("title") or video_id,
                "url": transcript.get("url") or video_url,
                "duration": exchange.transcript_duration(transcript),
                "language": transcript.get("language", ""),
                "mode": mode,
                "download_height": height,
                "source_file": source_file or previous.get("source_file", ""),
                "updated_at": time.time(),
                "notices": notices,
                "groups": groups
            })

        _update_job(
            ANALYZE_JOBS, job_id,
            status="done", stage="Analisis selesai", progress=100,
            video_id=video_id
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

    height = request.form.get("quality", DEFAULT_DOWNLOAD_HEIGHT)
    if height not in DOWNLOAD_HEIGHTS:
        height = DEFAULT_DOWNLOAD_HEIGHT

    job_id = _new_job(
        ANALYZE_JOBS, stage="Menyiapkan", progress=None, video_id=""
    )
    threading.Thread(
        target=_analyze_worker, args=(job_id, video_url, mode, height),
        daemon=True
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


def _render_video_page(analysis, status=200, **extra):
    groups = analysis.get("groups", [])
    _mark_agreement(groups)

    source_path = _source_path(analysis)
    height = str(analysis.get("download_height") or DEFAULT_DOWNLOAD_HEIGHT)

    return render_template(
        "video.html",
        video=analysis,
        groups=groups,
        keys=_provider_keys(),
        source_ready=source_path is not None,
        source_mb=_size_mb(source_path),
        can_render=source_path is not None or bool(analysis.get("url")),
        download_height=height,
        renders=_list_renders(video_id=analysis["video_id"]),
        limits=exchange,
        **extra
    ), status


@app.route("/video/<video_id>")
def video_page(video_id):
    return _render_video_page(
        _load_video(video_id),
        imported=request.args.get("imported", ""),
        deleted=request.args.get("deleted", "")
    )


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


@app.route("/video/<video_id>/import", methods=["POST"])
def import_analysis(video_id):
    analysis = _load_video(video_id)
    transcript = _load_transcript(video_id)

    raw = request.form.get("json_text", "")
    upload = request.files.get("json_file")

    if upload and upload.filename:
        raw = upload.read(exchange.MAX_IMPORT_BYTES + 1).decode(
            "utf-8", errors="replace"
        )

    analyst = request.form.get("analyst", "claude")
    label = {
        "claude": "CLAUDE", "chatgpt": "CHATGPT"
    }.get(analyst, "AI LAIN") + " (IMPOR)"

    try:
        result = exchange.validate_import(exchange.parse_import(raw), transcript)
    except exchange.ExchangeError as error:
        return _render_video_page(
            analysis, 400, import_error=str(error), import_text=raw[:200000]
        )

    if not result["accepted"]:
        return _render_video_page(
            analysis, 400,
            import_error="Tidak ada kandidat yang lolos validasi.",
            import_rejected=result["rejected"],
            import_text=raw[:200000]
        )

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

    return redirect(
        url_for("video_page", video_id=video_id, imported=label, _anchor="impor")
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

    _update_analysis(video_id, lambda current: current.update(source_file=""))

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

    video_url = str(analysis.get("url") or "")
    if not _source_path(analysis) and not video_url:
        return (
            "File video sumber tidak ada dan alamat videonya tidak tersimpan. "
            "Analisis ulang video ini dari halaman utama."
        ), 400

    height = str(analysis.get("download_height") or DEFAULT_DOWNLOAD_HEIGHT)
    if height not in DOWNLOAD_HEIGHTS:
        height = DEFAULT_DOWNLOAD_HEIGHT

    safe_title = re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_")[:30] or "klip"
    filename = (
        f"zoi_{video_id}_{int(start)}-{int(end)}_{safe_title}_"
        f"{uuid.uuid4().hex[:6]}.mp4"
    )
    output_path = os.path.join(OUTPUT_DIR, filename)

    job_id = _new_job(
        RENDER_JOBS,
        progress=0, stage="Menunggu giliran render", status="queued",
        step="", filename=filename, video_id=video_id
    )

    def render_worker():
        with _RENDER_SLOT:
            try:
                # Diperiksa di sini, bukan saat tombol ditekan: render lain
                # yang lebih dulu antre mungkin sudah mengunduh videonya.
                source_path = _source_path(
                    exchange.load_analysis(video_id) or analysis
                )
                step = ""

                if not source_path:
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
                        source_path = _download_source(video_url, height, report)
                    except Exception as error:
                        raise RuntimeError(
                            f"Video gagal diunduh: {str(error)[:500]}"
                        ) from error

                    name = os.path.basename(source_path)
                    _update_analysis(
                        video_id,
                        lambda current: current.update(source_file=name)
                    )

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
                    progress_callback=update_progress
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
        start=start, end=end, job_id=job_id, video=analysis
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


if __name__ == "__main__":
    # Bawaan hanya bisa dibuka dari HP ini sendiri. Untuk membuka dari
    # perangkat lain di Wi-Fi yang sama: ZOI_HOST=0.0.0.0 python app.py
    host = os.getenv("ZOI_HOST", "127.0.0.1")
    port = int(os.getenv("ZOI_PORT", "5000"))
    print(f"ZOI AI CLIPPER berjalan di http://{host}:{port}", flush=True)
    app.run(host=host, port=port, debug=False, threaded=True)

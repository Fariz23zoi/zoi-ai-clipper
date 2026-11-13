"""Local clipping: ffmpeg subclip + OpenCV face-aware vertical crop.

Two stages per highlight:
  1. Cut the source video to [start, end] with ffmpeg (re-encoded, audio kept).
  2. Reframe the cut to the target aspect ratio. For 9:16 we slide a vertical
     window horizontally across the frame to keep faces centred (Haar
     cascade — same approach as the original repo, no external models).
"""
import os
import subprocess
from typing import Dict, List, Optional, Tuple

from ..config import LOCAL_OUTPUT_DIR

# Bisa diatur lewat .env. "veryfast" seimbang untuk HP; pakai "ultrafast"
# bila perangkat lambat (file lebih besar), atau "fast" untuk kualitas.
RENDER_PRESET = os.getenv("ZOI_RENDER_PRESET", "veryfast").strip() or "veryfast"
RENDER_CRF = os.getenv("ZOI_RENDER_CRF", "23").strip() or "23"

# Ukuran kanvas 9:16 untuk tiap pilihan kualitas render (lebar x tinggi).
RENDER_SIZES = {
    "240": (240, 426),
    "360": (360, 640),
    "480": (480, 854),
    "720": (720, 1280),
    "1080": (1080, 1920),
}
DEFAULT_RENDER_SIZE = RENDER_SIZES["720"]

# Bingkai video di tengah kanvas 9:16:
#   "wide"   seluruh frame asli (bawaan, seperti sebelumnya)
#   "square" bagian tengah frame dipotong persegi 1:1, jadi orang tampil lebih besar
RENDER_FRAMES = ("wide", "square")

# FFmpeg dijalankan dengan prioritas lebih rendah supaya HP tetap bisa
# dipakai selama rendering. 0 mematikan fitur ini.
try:
    RENDER_NICE = max(0, min(19, int(os.getenv("ZOI_RENDER_NICE", "10"))))
except ValueError:
    RENDER_NICE = 10


# Shorts cukup 30 fps. Sumber 50/60 fps diturunkan separuhnya, karena
# merender 60 fps membutuhkan kerja hampir dua kali lipat. 0 = tanpa batas.
try:
    RENDER_MAX_FPS = max(0.0, float(os.getenv("ZOI_RENDER_MAX_FPS", "30")))
except ValueError:
    RENDER_MAX_FPS = 30.0


def _fps_limit_filter(source_path: str) -> str:
    """Kembalikan 'fps=N/D,' bila sumber lebih cepat dari batas, atau ''.

    Frame rate dibagi dua (60 -> 30, 50 -> 25) supaya gerakan tetap mulus.
    Bila frame rate tidak bisa dibaca, video dibiarkan apa adanya.
    """
    if not RENDER_MAX_FPS:
        return ""

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "default=noprint_wrappers=1", source_path],
            capture_output=True, text=True, check=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    rates = dict(
        line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line
    )

    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            number, _, divisor = rates.get(key, "").partition("/")
            number, divisor = int(number), int(divisor or "1")
        except ValueError:
            continue

        if number <= 0 or divisor <= 0:
            continue

        # Nilai yang tidak wajar berarti frame rate tidak diketahui pasti.
        if number / divisor > 240:
            return ""

        halvings = 0
        while number / (divisor * 2 ** halvings) > RENDER_MAX_FPS + 1:
            halvings += 1

        if not halvings:
            return ""
        return f"fps={number}/{divisor * 2 ** halvings},"

    return ""


def _with_low_priority(command):
    """Awali perintah dengan `nice` bila tersedia di sistem."""
    import shutil

    if RENDER_NICE and shutil.which("nice"):
        return ["nice", "-n", str(RENDER_NICE)] + list(command)
    return list(command)



def _run_ffmpeg_progress(cmd, duration, callback=None, base=0, span=100, stage="Memproses"):
    """Jalankan FFmpeg dan laporkan progres nyata dari -progress pipe:1."""
    import tempfile

    command = list(cmd)
    command[1:1] = ["-progress", "pipe:1", "-nostats"]

    # stderr ditulis ke file sementara supaya pipa yang penuh tidak
    # menghentikan FFmpeg selagi stdout sedang dibaca.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errors:
        proc = subprocess.Popen(
            _with_low_priority(command), stdout=subprocess.PIPE,
            stderr=errors, stdin=subprocess.DEVNULL, text=True, bufsize=1,
            encoding="utf-8", errors="replace"
        )
        try:
            for line in proc.stdout:
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        elapsed = int(line.split("=", 1)[1]) / 1_000_000
                        pct = max(0.0, min(0.99, elapsed / max(float(duration), 0.1)))
                        if callback:
                            callback(base + int(span * pct), stage)
                    except ValueError:
                        pass
                elif line.startswith("progress=end"):
                    if callback:
                        callback(base + span, stage)
            code = proc.wait()
            if code:
                errors.seek(0)
                detail = errors.read().strip()
                raise RuntimeError(detail[-1500:] or f"FFmpeg error {code}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()


def _ratio(aspect_ratio: str) -> float:
    """Parse '9:16' → 9/16, '1:1' → 1.0."""
    try:
        w, h = aspect_ratio.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9.0 / 16.0


def _cut_subclip(source_path: str, start: float, end: float, out_path: str,
                  progress_callback=None) -> str:
    """Cut video and report FFmpeg progress."""
    duration = max(0.1, end - start)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", source_path,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    _run_ffmpeg_progress(
        cmd, duration, progress_callback, 5, 40, "Memotong video"
    )
    return out_path


def _reframe_vertical(in_path: str, out_path: str, aspect_ratio: str,
                      progress_callback=None) -> str:
    """Crop the cut clip to the target aspect ratio, tracking faces if possible."""
    try:
        import cv2  # type: ignore
    except ImportError:
        # Fallback tanpa OpenCV: seluruh frame asli dipertahankan dan
        # kanvas vertikal diisi latar blur. Jalur utama di Termux adalah
        # _render_vertical_single_pass(); blok ini dipakai untuk rasio lain.
        filter_complex = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
            "crop=720:1280,boxblur=20:10[bg];"
            "[fg]scale=720:1280:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", in_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            out_path,
        ]
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", in_path],
            capture_output=True, text=True, check=True
        )
        duration = float(probe.stdout.strip() or "1")
        _run_ffmpeg_progress(
            cmd, duration, progress_callback,
            45, 50, "Reframing 9:16 + background blur"
        )
        return out_path

    target_ratio = _ratio(aspect_ratio)
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {in_path}")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Compute the largest crop that fits inside the frame at the target ratio.
    if target_ratio < src_w / src_h:
        crop_h = src_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = src_w
        crop_h = int(crop_w / target_ratio)
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    silent_path = out_path + ".silent.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(silent_path, fourcc, fps, (crop_w, crop_h))

    last_center: Optional[Tuple[int, int]] = None
    smoothing = 0.15  # how aggressively to chase a new face position
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(faces) > 0:
            # Pick the largest face — usually the speaker.
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            cx = x + w // 2
            cy = y + h // 2
            if last_center is None:
                last_center = (cx, cy)
            else:
                lx, ly = last_center
                last_center = (
                    int(lx + (cx - lx) * smoothing),
                    int(ly + (cy - ly) * smoothing),
                )
        if last_center is None:
            last_center = (src_w // 2, src_h // 2)

        cx, cy = last_center
        x0 = max(0, min(src_w - crop_w, cx - crop_w // 2))
        y0 = max(0, min(src_h - crop_h, cy - crop_h // 2))
        cropped = frame[y0:y0 + crop_h, x0:x0 + crop_w]
        writer.write(cropped)

    cap.release()
    writer.release()

    # Mux audio from the cut clip back onto the silent reframed video.
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", silent_path,
        "-i", in_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    os.remove(silent_path)
    return out_path


def _has_opencv() -> bool:
    try:
        import cv2  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


def _foreground_filter(width: int, height: int, frame: str) -> str:
    """Filter FFmpeg untuk video di tengah kanvas."""
    if frame == "square":
        # Potong persegi dari tengah frame, selebar kanvas.
        return (
            "[fg]crop='min(iw,ih)':'min(iw,ih)',"
            f"scale={width}:{width}[fg];"
        )
    return f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"


def _render_vertical_single_pass(source_path: str, start: float, end: float,
                                 out_path: str, progress_callback=None,
                                 size: Optional[Tuple[int, int]] = None,
                                 frame: str = "wide") -> str:
    """Potong dan ubah ke 9:16 dalam satu kali encode (tanpa OpenCV).

    Seluruh frame asli tetap terlihat di tengah; ruang kosong diisi
    salinan frame yang diblur. Blur selalu dikerjakan pada 180x320 lalu
    diperbesar ke ukuran kanvas: latar blur tidak punya detail, jadi
    tampilannya sama untuk semua ukuran dan bebannya tetap kecil.
    """
    width, height = size or DEFAULT_RENDER_SIZE
    duration = max(0.1, end - start)
    filter_complex = (
        f"[0:v]{_fps_limit_filter(source_path)}split=2[bg][fg];"
        "[bg]scale=180:320:force_original_aspect_ratio=increase,"
        f"crop=180:320,boxblur=6:3,scale={width}:{height}[bg];"
        + _foreground_filter(width, height, frame)
        + "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", source_path,
        "-t", f"{duration:.3f}",
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", RENDER_PRESET, "-crf", RENDER_CRF,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    _run_ffmpeg_progress(
        cmd, duration, progress_callback,
        5, 90, f"Memotong + reframing 9:16 ({width}x{height})"
    )
    return out_path


def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    progress_callback=None,
    size: Optional[Tuple[int, int]] = None,
    frame: str = "wide",
) -> str:
    """Cut + reframe one highlight and report rendering progress.

    Hasil ditulis ke file sementara dan baru diberi nama akhir setelah
    FFmpeg selesai, sehingga file setengah jadi tidak pernah terlihat.

    `size` (lebar, tinggi) dipakai dashboard: hasilnya selalu kanvas 9:16
    berukuran itu dengan latar blur. Tanpa `size`, perilaku lama berlaku
    (pelacakan wajah bila OpenCV terpasang, selain itu latar blur 720x1280).
    """
    stem, ext = os.path.splitext(out_path)
    work_path = f"{stem}.part{ext or '.mp4'}"
    cut_path = f"{stem}.cut{ext or '.mp4'}"
    try:
        if progress_callback:
            progress_callback(1, "Menyiapkan FFmpeg")

        if frame not in RENDER_FRAMES:
            raise ValueError(f"Bingkai tidak dikenal: {frame}")

        if size or frame != "wide" or (
            _ratio(aspect_ratio) == 9.0 / 16.0 and not _has_opencv()
        ):
            _render_vertical_single_pass(
                source_path, start_time, end_time, work_path,
                progress_callback=progress_callback, size=size, frame=frame
            )
        else:
            _cut_subclip(
                source_path, start_time, end_time, cut_path,
                progress_callback=progress_callback
            )
            _reframe_vertical(
                cut_path, work_path, aspect_ratio,
                progress_callback=progress_callback
            )

        if not os.path.isfile(work_path) or os.path.getsize(work_path) == 0:
            raise RuntimeError("FFmpeg selesai tetapi file hasil kosong.")

        os.replace(work_path, out_path)

        if progress_callback:
            progress_callback(100, "Rendering selesai")
    finally:
        for temp_path in (cut_path, work_path, work_path + ".silent.mp4"):
            if os.path.exists(temp_path):
                os.remove(temp_path)
    return out_path


def crop_highlights_local(
    source_path: str,
    highlights: List[Dict],
    aspect_ratio: str = "9:16",
    out_dir: Optional[str] = None,
) -> List[Dict]:
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    results: List[Dict] = []
    for i, h in enumerate(highlights, 1):
        out_path = os.path.join(out_dir, f"short_{i:02d}.mp4")
        print(f"[clip/local] {i}/{len(highlights)}: {h.get('title', '(untitled)')}", flush=True)
        try:
            crop_clip_local(
                source_path,
                float(h["start_time"]),
                float(h["end_time"]),
                aspect_ratio,
                out_path,
            )
            results.append({**h, "clip_url": out_path})
        except Exception as e:
            print(f"[clip/local] {i} failed: {e}", flush=True)
            results.append({**h, "clip_url": None, "error": str(e)})
    return results

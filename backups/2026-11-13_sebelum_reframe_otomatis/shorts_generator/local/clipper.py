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



def _run_ffmpeg_progress(cmd, duration, callback=None, base=0, span=100, stage="Memproses",
                         cwd=None):
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
            encoding="utf-8", errors="replace", cwd=cwd
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


def _foreground_filter(width: int, height: int, frame: str,
                       crop_x: float = 0.5) -> str:
    """Filter FFmpeg untuk video di tengah kanvas.

    `crop_x` (0 = kiri, 0.5 = tengah, 1 = kanan) menentukan bagian mana
    yang diambil pada bingkai persegi.
    """
    if frame == "square":
        position = max(0.0, min(1.0, float(crop_x)))
        return (
            "[fg]crop='min(iw,ih)':'min(iw,ih)':"
            f"'(iw-min(iw,ih))*{position:.3f}':'(ih-min(iw,ih))/2',"
            f"scale={width}:{width}[fg];"
        )
    return f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"


# ------------------------------------------------------------- blok hook

HOOK_MAX_CHARS = 150
HOOK_MAX_LINES = 3

_HOOK_FONTS = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/system/fonts/Roboto-Bold.ttf",
    "/system/fonts/Roboto-Regular.ttf",
)


_CARD_FONTS = (
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/arialbi.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-BoldOblique.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-BoldOblique.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
)

HOOK_STYLES = ("box", "card")
HOOK_POSITIONS = ("top", "middle")
CARD_COLOR = "0xF7C948"
CARD_SHADOW = "0xC9961A"


def find_card_font() -> Optional[str]:
    """Font tebal untuk kartu hook kuning; bila tidak ada, font hook biasa."""
    custom = os.getenv("ZOI_HOOK_FONT", "").strip()
    if custom and os.path.isfile(custom):
        return custom
    for path in _CARD_FONTS:
        if os.path.isfile(path):
            return path
    return find_hook_font()


def find_hook_font() -> Optional[str]:
    """File font tebal untuk teks hook; ZOI_HOOK_FONT bisa menunjuk font lain."""
    custom = os.getenv("ZOI_HOOK_FONT", "").strip()
    for path in ((custom,) if custom else ()) + _HOOK_FONTS:
        if path and os.path.isfile(path):
            return path
    return None


def clean_hook_text(text) -> str:
    """Rapikan teks hook: buang [tertawa] dan sejenisnya, emoji, spasi berlebih."""
    import re

    text = re.sub(r"\[[^\]]*\]", " ", str(text or ""))
    # Emoji dan simbol di luar BMP tidak ada di font biasa.
    text = "".join(ch for ch in text if ord(ch) <= 0xFFFF and (ch.isprintable() or ch == " "))
    return re.sub(r"\s+", " ", text).strip()[:HOOK_MAX_CHARS]


def layout_hook(text: str, width: int, inner: float = 0.84,
                char: float = 0.56, upper: bool = False,
                scales=(0.062, 0.054, 0.047)):
    """Bagi teks hook menjadi paling banyak 3 baris yang muat di kanvas.

    Mengembalikan (ukuran_font, daftar_baris). Font diperkecil bila teks
    terlalu panjang; sisa yang tetap tidak muat diganti "...".
    """
    import textwrap

    text = clean_hook_text(text)
    if upper:
        text = text.upper()
    if not text:
        return 0, []

    for scale in scales:
        size = max(12, int(width * scale))
        per_line = max(8, int(width * inner / (size * char)))
        lines = textwrap.wrap(text, width=per_line, break_long_words=True)
        if len(lines) <= HOOK_MAX_LINES:
            return size, lines

    lines = lines[:HOOK_MAX_LINES]
    lines[-1] = lines[-1][: max(1, per_line - 3)].rstrip() + "..."
    return size, lines


def _probe_aspect(path: str) -> float:
    """Rasio lebar/tinggi video; 16:9 bila tidak terbaca."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True, timeout=60
        ).stdout.strip().split(",")
        width, height = int(out[0]), int(out[1])
        if width > 0 and height > 0:
            return width / height
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return 16 / 9


def _hook_filters(text: str, width: int, height: int, frame: str,
                  aspect: float, work_dir: str, style: str = "box",
                  position: str = "top", prefix: str = "hook") -> str:
    """Filter blok hook: "box" (teks putih di kotak gelap) atau "card"
    (kartu kuning dengan teks hitam kapital), di "top" atau "middle".

    Font dan teks disalin ke folder kerja lalu dipanggil dengan nama
    pendek, supaya tidak ada masalah tanda khusus pada path Windows
    maupun pada isi teks (titik dua, kutip, persen).
    """
    import shutil

    if style not in HOOK_STYLES:
        raise ValueError(f"Gaya hook tidak dikenal: {style}")
    if position not in HOOK_POSITIONS:
        raise ValueError(f"Posisi hook tidak dikenal: {position}")

    card = style == "card"
    if card:
        size, lines = layout_hook(
            text, width, inner=0.78, char=0.64, upper=True,
            scales=(0.074, 0.064, 0.055)
        )
    else:
        size, lines = layout_hook(text, width)
    if not lines:
        return ""

    font = find_card_font() if card else find_hook_font()
    if not font:
        raise RuntimeError(
            "Font untuk blok hook tidak ditemukan. Matikan blok hook, atau "
            "isi ZOI_HOOK_FONT dengan lokasi file font .ttf."
        )
    font_name = f"{prefix}font.ttf"
    shutil.copyfile(font, os.path.join(work_dir, font_name))

    picture = width if frame == "square" else min(height, width / max(aspect, 0.1))
    top_space = (height - picture) / 2
    line_step = int(size * (1.3 if card else 1.55))
    block = line_step * len(lines)
    pad = int(size * (0.75 if card else 0.32))
    total = block + pad * 2

    if position == "middle":
        top = int((height - total) / 2)
    elif top_space >= total:
        top = int((top_space - total) / 2)
    else:
        top = int(height * 0.04)

    filters = []

    if card:
        card_w = int(width * 0.88)
        x0 = (width - card_w) // 2
        shift = max(4, width // 90)
        border = max(3, width // 200)
        filters += [
            f"drawbox=x={x0 + shift}:y={top + shift}:w={card_w}:h={total}"
            f":color={CARD_SHADOW}:t=fill",
            f"drawbox=x={x0}:y={top}:w={card_w}:h={total}:color={CARD_COLOR}:t=fill",
            f"drawbox=x={x0}:y={top}:w={card_w}:h={total}:color=black:t={border}",
        ]

    for index, line in enumerate(lines):
        name = f"{prefix}{index}.txt"
        with open(os.path.join(work_dir, name), "w", encoding="utf-8") as file:
            file.write(line)
        y = top + pad + index * line_step
        if card:
            style_options = (
                ":fontcolor=black:borderw=2:bordercolor=white@0.85"
            )
        else:
            style_options = (
                f":fontcolor=white:box=1:boxcolor=black@0.62:boxborderw={pad}"
            )
        filters.append(
            f"drawtext=fontfile={font_name}:textfile={name}:expansion=none"
            f":fontsize={size}{style_options}:x=(w-text_w)/2:y={y}"
        )
    return "," + ",".join(filters)


def _has_audio(path: str) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(out.strip())


def _wav_seconds(path: str) -> float:
    import wave
    with wave.open(path, "rb") as file:
        return file.getnframes() / float(file.getframerate() or 1)


INTRO_FPS = 30
INTRO_TAIL = 0.5
INTRO_MIN = 1.5


def _render_vertical_single_pass(source_path: str, start: float, end: float,
                                 out_path: str, progress_callback=None,
                                 size: Optional[Tuple[int, int]] = None,
                                 frame: str = "wide", crop_x: float = 0.5,
                                 hook_text: Optional[str] = None,
                                 hook_style: str = "box",
                                 hook_position: str = "top",
                                 intro_text: Optional[str] = None,
                                 intro_audio: Optional[str] = None) -> str:
    """Potong dan ubah ke 9:16 dalam satu kali encode (tanpa OpenCV).

    Seluruh frame asli tetap terlihat di tengah; ruang kosong diisi
    salinan frame yang diblur. Blur selalu dikerjakan pada 180x320 lalu
    diperbesar ke ukuran kanvas: latar blur tidak punya detail, jadi
    tampilannya sama untuk semua ukuran dan bebannya tetap kecil.

    Dengan `intro_text`, klip diawali pembuka: gambar pertama klip yang
    dibekukan, kartu hook kuning di tengah, dan suara `intro_audio` (WAV)
    bila ada. Semuanya tetap satu kali encode.
    """
    import shutil
    import tempfile

    width, height = size or DEFAULT_RENDER_SIZE
    duration = max(0.1, end - start)
    work_dir = tempfile.mkdtemp(prefix="zoi_hook_")

    try:
        aspect = _probe_aspect(source_path)
        hook = ""
        if hook_text and clean_hook_text(hook_text):
            hook = _hook_filters(
                hook_text, width, height, frame, aspect, work_dir,
                style=hook_style, position=hook_position, prefix="hook"
            )

        base = (
            f"[0:v]{_fps_limit_filter(source_path)}split=2[bg][fg];"
            "[bg]scale=180:320:force_original_aspect_ratio=increase,"
            f"crop=180:320,boxblur=6:3,scale={width}:{height}[bg];"
            + _foreground_filter(width, height, frame, crop_x)
            + "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )

        intro = ""
        if intro_text and clean_hook_text(intro_text):
            intro = _hook_filters(
                intro_text, width, height, frame, aspect, work_dir,
                style="card", position="middle", prefix="intro"
            )

        if not intro:
            _encode_vertical(
                source_path, start, duration, base + hook + "[v]", out_path,
                progress_callback, width, height, work_dir
            )
            return out_path

        voice = 0.0
        if intro_audio:
            voice = _wav_seconds(intro_audio)
            shutil.copyfile(intro_audio, os.path.join(work_dir, "intro.wav"))
        intro_seconds = round(max(INTRO_MIN, voice + INTRO_TAIL), 2)
        frames = max(1, int(round(intro_seconds * INTRO_FPS)))

        audio_format = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if intro_audio:
            intro_sound = (
                f"[1:a]{audio_format},apad,atrim=end={intro_seconds},"
                "asetpts=N/SR/TB[ia];"
            )
        else:
            intro_sound = (
                f"anullsrc=r=44100:cl=stereo,atrim=end={intro_seconds},"
                "asetpts=N/SR/TB[ia];"
            )
        if _has_audio(source_path):
            main_sound = f"[0:a]{audio_format},asetpts=N/SR/TB[ma];"
        else:
            main_sound = (
                f"anullsrc=r=44100:cl=stereo,atrim=end={duration:.3f},"
                "asetpts=N/SR/TB[ma];"
            )

        graph = (
            base + f",fps={INTRO_FPS},format=yuv420p,split=2[c][i];"
            + "[c]null" + hook + "[main];"
            + f"[i]trim=end_frame=1,loop=loop={frames - 1}:size=1:start=0,"
            + f"setpts=N/{INTRO_FPS}/TB" + intro + "[introv];"
            + intro_sound + main_sound
            + "[introv][ia][main][ma]concat=n=2:v=1:a=1[v][a]"
        )
        _encode_vertical(
            source_path, start, duration, graph, out_path,
            progress_callback, width, height, work_dir,
            extra_inputs=["-i", "intro.wav"] if intro_audio else [],
            audio_map="[a]", total=duration + intro_seconds
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return out_path


def _encode_vertical(source_path, start, duration, filter_complex, out_path,
                     progress_callback, width, height, work_dir,
                     extra_inputs=(), audio_map="0:a:0?", total=None):
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", source_path,
        *extra_inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", RENDER_PRESET, "-crf", RENDER_CRF,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    _run_ffmpeg_progress(
        cmd, total or duration, progress_callback,
        5, 90, f"Memotong + reframing 9:16 ({width}x{height})",
        cwd=work_dir
    )


def save_thumbnail(video_path: str, image_path: str, at: float = 0.3) -> str:
    """Simpan satu gambar dari video sebagai thumbnail JPG."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", video_path,
         "-frames:v", "1", "-q:v", "2", image_path],
        check=True, capture_output=True, timeout=120
    )
    return image_path


def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    progress_callback=None,
    size: Optional[Tuple[int, int]] = None,
    frame: str = "wide",
    crop_x: float = 0.5,
    hook_text: Optional[str] = None,
    hook_style: str = "box",
    hook_position: str = "top",
    intro_text: Optional[str] = None,
    intro_audio: Optional[str] = None,
) -> str:
    """Cut + reframe one highlight and report rendering progress.

    Hasil ditulis ke file sementara dan baru diberi nama akhir setelah
    FFmpeg selesai, sehingga file setengah jadi tidak pernah terlihat.

    `size` (lebar, tinggi) dipakai dashboard: hasilnya selalu kanvas 9:16
    berukuran itu dengan latar blur. Tanpa `size`, perilaku lama berlaku
    (pelacakan wajah bila OpenCV terpasang, selain itu latar blur 720x1280).
    """
    # FFmpeg dijalankan dari folder kerja sementara, jadi pakai path absolut.
    source_path = os.path.abspath(source_path)
    out_path = os.path.abspath(out_path)
    stem, ext = os.path.splitext(out_path)
    work_path = f"{stem}.part{ext or '.mp4'}"
    cut_path = f"{stem}.cut{ext or '.mp4'}"
    try:
        if progress_callback:
            progress_callback(1, "Menyiapkan FFmpeg")

        if frame not in RENDER_FRAMES:
            raise ValueError(f"Bingkai tidak dikenal: {frame}")

        if size or frame != "wide" or hook_text or intro_text or (
            _ratio(aspect_ratio) == 9.0 / 16.0 and not _has_opencv()
        ):
            _render_vertical_single_pass(
                source_path, start_time, end_time, work_path,
                progress_callback=progress_callback, size=size, frame=frame,
                crop_x=crop_x, hook_text=hook_text, hook_style=hook_style,
                hook_position=hook_position, intro_text=intro_text,
                intro_audio=os.path.abspath(intro_audio) if intro_audio else None
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

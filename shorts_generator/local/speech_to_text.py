"""Transkrip dari suara video, untuk video yang tidak punya subtitle.

Memakai faster-whisper (Whisper versi CTranslate2) di CPU. Fitur ini
opsional: dipasang lewat requirements-speech.txt dan hanya dipakai bila
YouTube tidak menyediakan subtitle. Tidak tersedia di Android Termux.

Hasilnya berbentuk sama dengan transkrip dari subtitle YouTube
({"segments", "words"}), sehingga ZOI Engine, ekspor/impor, dan render
tidak perlu tahu dari mana transkrip berasal.
"""
import importlib.util
import os
import threading

from ..config import PROJECT_ROOT
from .transcriber_youtube import transcript_from_words

# Ukuran model: tiny, base, small, medium, large-v3, turbo. "small" cukup
# akurat untuk bahasa Indonesia dan masih wajar di CPU laptop.
MODEL_NAME = os.getenv("ZOI_WHISPER_MODEL", "small").strip() or "small"

# "auto": bahasa dideteksi dari suara. Bisa dipaksa, misalnya "id".
LANGUAGE = os.getenv("ZOI_WHISPER_LANGUAGE", "auto").strip().lower() or "auto"

# 1 = paling cepat. 5 sedikit lebih akurat tetapi kira-kira dua kali lebih lama.
try:
    BEAM_SIZE = max(1, int(os.getenv("ZOI_WHISPER_BEAM", "1")))
except ValueError:
    BEAM_SIZE = 1

MODEL_DIR = os.path.join(PROJECT_ROOT, "models")

APPROX_SIZE_MB = {
    "tiny": 75, "base": 145, "small": 465, "medium": 1460,
    "large-v3": 2950, "turbo": 1550,
}

# Whisper sering tertukar antara bahasa Indonesia, Melayu, Jawa, dan Sunda.
# Aplikasi ini untuk konten Indonesia, jadi keempatnya ditulis sebagai
# bahasa Indonesia.
MALAY_FAMILY = ("id", "ms", "jw", "su")

_LOCK = threading.Lock()
_MODEL = None
_MODEL_KEY = None


def _ignore(stage, progress=None):
    pass


def is_available():
    """True bila faster-whisper terpasang."""
    return importlib.util.find_spec("faster_whisper") is not None


def _repo_id(name):
    if "/" in name:
        return name
    try:
        from faster_whisper.utils import _MODELS
    except ImportError:
        return None
    return _MODELS.get(name)


def _cache_folder(name):
    repo = _repo_id(name)
    if not repo:
        return None
    return os.path.join(MODEL_DIR, "models--" + repo.replace("/", "--"))


def _folder_mb(path):
    total = 0
    for root, _dirs, files in os.walk(path or ""):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1_048_576


def _model_files(name, report):
    """Folder berisi file model; diunduh dulu bila belum ada."""
    if os.path.isdir(name):
        return name

    from faster_whisper.utils import download_model

    try:
        return download_model(name, cache_dir=MODEL_DIR, local_files_only=True)
    except Exception:  # belum pernah diunduh
        pass

    approx = APPROX_SIZE_MB.get(name)
    label = "Mengunduh model pengenal suara (sekali saja"
    label += f", sekitar {approx} MB)" if approx else ")"
    folder = _cache_folder(name)
    result = {}

    def run():
        try:
            result["path"] = download_model(name, cache_dir=MODEL_DIR)
        except Exception as error:
            result["error"] = error

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    while worker.is_alive():
        done = _folder_mb(folder) if folder else 0
        if approx and done >= 1:
            report(f"{label}: {done:.0f} MB", min(99, int(done * 100 / approx)))
        else:
            report(label, None)
        worker.join(1.0)

    if "error" in result:
        raise RuntimeError(
            "Model pengenal suara gagal diunduh. Periksa internet, lalu coba "
            f"lagi. Keterangan: {str(result['error'])[:300]}"
        ) from result["error"]

    return result["path"]


def _get_model(report):
    """Model Whisper; dimuat sekali lalu dipakai ulang."""
    global _MODEL, _MODEL_KEY

    if _MODEL is not None and _MODEL_KEY == MODEL_NAME:
        return _MODEL

    path = _model_files(MODEL_NAME, report)
    report("Memuat model pengenal suara", None)

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(path, device="cpu", compute_type="int8")
    except Exception as error:
        raise RuntimeError(
            f"Model pengenal suara gagal dimuat: {str(error)[:300]}"
        ) from error

    _MODEL, _MODEL_KEY = model, MODEL_NAME
    return model


def _choose_language(model, audio, hint):
    """Bahasa untuk transkripsi.

    Urutan: pengaturan ZOI_WHISPER_LANGUAGE, deteksi dari suara, bahasa
    video menurut YouTube, lalu bahasa Indonesia.
    """
    if LANGUAGE != "auto":
        return LANGUAGE

    try:
        detected = model.detect_language(
            audio, vad_filter=True, language_detection_segments=3
        )[0]
    except Exception:
        detected = None

    if detected:
        return "id" if detected in MALAY_FAMILY else detected

    base = str(hint or "").lower().replace("_", "-").split("-")[0]
    if base in (getattr(model, "supported_languages", None) or ()):
        return "id" if base in MALAY_FAMILY else base

    return "id"


def _clock(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _looks_like_noise(segment):
    """Aturan Whisper untuk bagian yang kemungkinan bukan ucapan."""
    no_speech = float(getattr(segment, "no_speech_prob", 0) or 0)
    log_prob = float(getattr(segment, "avg_logprob", 0) or 0)
    return no_speech > 0.6 and log_prob < -1.0


def _segment_words(segment):
    """Kata bertimestamp dari satu segmen Whisper."""
    items = []

    for word in getattr(segment, "words", None) or []:
        text = str(getattr(word, "word", "") or "").strip()
        if not text:
            continue
        start = float(word.start)
        items.append({
            "time": start, "end": max(start, float(word.end)), "text": text
        })

    if items:
        return items

    # Tanpa waktu per kata: bagi rata waktu segmen.
    texts = str(getattr(segment, "text", "") or "").split()
    start = float(segment.start)
    step = max(0.0, float(segment.end) - start) / len(texts) if texts else 0
    return [
        {"time": start + step * i, "end": start + step * (i + 1), "text": text}
        for i, text in enumerate(texts)
    ]


def transcribe_file(media_path, report=None, language_hint=None,
                    model=None, audio=None):
    """Kenali ucapan dalam file audio/video.

    Mengembalikan {"segments", "words", "language", "duration", "model"}.
    `report(tahap, persen)` menerima kemajuan; persen None berarti tahap
    tanpa angka. Hanya satu transkripsi berjalan pada satu waktu.
    """
    report = report or _ignore

    if not _LOCK.acquire(blocking=False):
        report("Menunggu giliran pengenal suara", None)
        _LOCK.acquire()

    try:
        if model is None:
            model = _get_model(report)

        if audio is None:
            report("Membaca audio", None)
            from faster_whisper.audio import decode_audio
            audio = decode_audio(media_path)

        report("Mendeteksi bahasa", None)
        language = _choose_language(model, audio, language_hint)

        segments, info = model.transcribe(
            audio,
            language=language,
            beam_size=BEAM_SIZE,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )

        total = float(getattr(info, "duration", 0) or 0)
        words = []
        report("Mengenali ucapan", 0)

        # Segmen dihasilkan satu per satu selama Whisper bekerja, jadi
        # kemajuan ini mengikuti proses yang sebenarnya.
        for segment in segments:
            if not _looks_like_noise(segment):
                words.extend(_segment_words(segment))

            if total > 0:
                done = max(0.0, min(float(segment.end), total))
                report(
                    f"Mengenali ucapan: {_clock(done)} dari {_clock(total)}",
                    min(99, int(done * 100 / total))
                )
    finally:
        _LOCK.release()

    result = transcript_from_words(words)
    result.update(
        language=language,
        duration=total,
        model=os.path.basename(MODEL_NAME.rstrip("/\\")) or MODEL_NAME
    )
    return result

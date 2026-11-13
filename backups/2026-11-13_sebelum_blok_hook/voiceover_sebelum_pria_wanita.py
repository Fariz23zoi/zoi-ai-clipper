"""Suara pembaca (voice-over) untuk pembuka klip, dengan Piper.

Piper mengubah teks menjadi suara di laptop sendiri: tanpa internet
(setelah suara diunduh sekali), tanpa API key, tanpa biaya. Suara bahasa
Indonesia "news_tts" sekitar 63 MB disimpan di folder models/piper.
Opsional: dipasang lewat requirements-voice.txt.
"""
import importlib.util
import os
import re
import threading
import urllib.request
import wave

from ..config import PROJECT_ROOT
from .clipper import clean_hook_text

VOICE_NAME = os.getenv("ZOI_VOICE", "id_ID-news_tts-medium").strip() or "id_ID-news_tts-medium"
VOICE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "id/id_ID/news_tts/medium/"
)
VOICE_DIR = os.path.join(PROJECT_ROOT, "models", "piper")
APPROX_SIZE_MB = 63

# Di bawah 1 = lebih cepat. Pembuka Shorts biasanya dibacakan cepat.
try:
    SPEED = max(0.5, min(1.5, float(os.getenv("ZOI_VOICE_SPEED", "0.85"))))
except ValueError:
    SPEED = 0.85

_LOCK = threading.Lock()
_VOICE = None


def _ignore(stage, progress=None):
    pass


def is_available():
    """True bila Piper terpasang."""
    return importlib.util.find_spec("piper") is not None


def speech_text(text):
    """Teks yang dibacakan: rapi dan mudah diucapkan."""
    text = clean_hook_text(text)
    # "Rp800.000" dibaca "RP delapan ratus ribu"; ubah jadi "800.000 rupiah".
    text = re.sub(r"\bRp\.?\s?([\d.,]+)", r"\1 rupiah", text, flags=re.IGNORECASE)
    if text and text[-1] not in ".?!":
        text += "."
    return text


def _download(url, path, report, label):
    temp = path + ".part"
    with urllib.request.urlopen(url, timeout=60) as response, open(temp, "wb") as file:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            file.write(chunk)
            done += len(chunk)
            if total:
                report(label, min(99, int(done * 100 / total)))
    os.replace(temp, path)


def _voice_files(report):
    """(file .onnx, file .json) suara; diunduh sekali bila belum ada."""
    if os.path.isfile(VOICE_NAME):
        return VOICE_NAME, VOICE_NAME + ".json"

    os.makedirs(VOICE_DIR, exist_ok=True)
    model = os.path.join(VOICE_DIR, VOICE_NAME + ".onnx")
    config = model + ".json"
    label = f"Mengunduh suara pembaca (sekali saja, sekitar {APPROX_SIZE_MB} MB)"

    try:
        if not os.path.isfile(config):
            _download(VOICE_URL + VOICE_NAME + ".onnx.json", config, report, label)
        if not os.path.isfile(model):
            _download(VOICE_URL + VOICE_NAME + ".onnx", model, report, label)
    except Exception as error:
        raise RuntimeError(
            "Suara pembaca gagal diunduh. Periksa internet, lalu coba lagi. "
            f"Keterangan: {str(error)[:300]}"
        ) from error

    return model, config


def _get_voice(report):
    global _VOICE
    if _VOICE is None:
        model, config = _voice_files(report)
        report("Memuat suara pembaca", None)
        from piper import PiperVoice
        _VOICE = PiperVoice.load(model, config_path=config)
    return _VOICE


def synthesize(text, wav_path, report=None, voice=None):
    """Bacakan `text` ke file WAV. Mengembalikan durasi suara (detik)."""
    report = report or _ignore
    spoken = speech_text(text)
    if not spoken:
        raise ValueError("Teks hook kosong.")

    with _LOCK:
        voice = voice or _get_voice(report)
        report("Membuat suara pembaca", None)

        try:
            from piper import SynthesisConfig
            config = SynthesisConfig(length_scale=SPEED)
        except ImportError:
            config = None

        with wave.open(wav_path, "wb") as file:
            voice.synthesize_wav(spoken, file, syn_config=config)

    with wave.open(wav_path, "rb") as file:
        frames, rate = file.getnframes(), file.getframerate()

    if not frames or not rate:
        raise RuntimeError("Suara pembaca kosong.")
    return frames / rate

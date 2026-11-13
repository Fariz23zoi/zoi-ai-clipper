"""Suara pembaca (voice-over) untuk pembuka klip, pria atau wanita.

Dua mesin, keduanya tanpa API key dan tanpa biaya:

* Suara Microsoft Edge (paket edge-tts): Ardi (pria) dan Gadis (wanita).
  Sangat alami, tetapi butuh internet saat render. Ini layanan baca-teks
  milik browser Edge yang dipakai tanpa jalur resmi, jadi bisa berhenti
  bekerja sewaktu-waktu.
* Piper: berjalan di laptop sendiri tanpa internet (setelah suaranya
  diunduh sekali, sekitar 63 MB, ke models/piper). Suara bahasa Indonesia
  Piper hanya satu, suara wanita; versi pria dibuat dengan menurunkan
  nadanya, jadi terdengar lebih robotik.

Bawaan: Edge dulu, lalu Piper bila Edge gagal (ZOI_VOICE_ENGINE=auto).
Opsional: dipasang lewat requirements-voice.txt.
"""
import asyncio
import importlib.util
import os
import re
import subprocess
import tempfile
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

# auto = Edge dulu, Piper bila gagal; edge = Edge saja; piper = Piper saja.
ENGINE = os.getenv("ZOI_VOICE_ENGINE", "auto").strip().lower() or "auto"
EDGE_RATE = os.getenv("ZOI_EDGE_RATE", "+10%").strip() or "+10%"

GENDERS = ("pria", "wanita")
EDGE_VOICES = {"pria": "id-ID-ArdiNeural", "wanita": "id-ID-GadisNeural"}
# Piper hanya punya suara wanita; versi pria = nada diturunkan.
PIPER_PITCH = {"pria": 0.62, "wanita": 1.0}

_LOCK = threading.Lock()
_VOICE = None


def _ignore(stage, progress=None):
    pass


def piper_available():
    return importlib.util.find_spec("piper") is not None


def edge_available():
    return importlib.util.find_spec("edge_tts") is not None


def is_available():
    """True bila setidaknya satu mesin suara terpasang."""
    if ENGINE == "edge":
        return edge_available()
    if ENGINE == "piper":
        return piper_available()
    return edge_available() or piper_available()


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


def _wav_seconds(path):
    with wave.open(path, "rb") as file:
        frames, rate = file.getnframes(), file.getframerate()
    if not frames or not rate:
        raise RuntimeError("Suara pembaca kosong.")
    return frames / rate


def _wav_rate(path):
    with wave.open(path, "rb") as file:
        return file.getframerate()


def _to_wav(source, target, extra_filter=""):
    """Ubah file suara menjadi WAV mono 24 kHz, dengan filter tambahan."""
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", source]
    if extra_filter:
        command += ["-af", extra_filter]
    command += ["-ac", "1", "-ar", "24000", target]
    result = subprocess.run(command, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(
            "Suara pembaca gagal diolah: "
            + result.stderr.decode("utf-8", errors="replace")[-300:]
        )


def _edge_synthesize(spoken, wav_path, gender):
    """Suara Edge (butuh internet) ke WAV."""
    import edge_tts

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None
    with tempfile.TemporaryDirectory(prefix="zoi_voice_") as folder:
        mp3 = os.path.join(folder, "suara.mp3")

        async def run():
            await edge_tts.Communicate(
                spoken, EDGE_VOICES[gender], rate=EDGE_RATE, proxy=proxy
            ).save(mp3)

        asyncio.run(asyncio.wait_for(run(), timeout=60))
        if not os.path.isfile(mp3) or not os.path.getsize(mp3):
            raise RuntimeError("Layanan suara Edge tidak mengirim suara.")
        _to_wav(mp3, wav_path)


def _piper_synthesize(spoken, wav_path, gender, report, voice=None):
    """Suara Piper (offline) ke WAV; nada diturunkan untuk suara pria."""
    voice = voice or _get_voice(report)
    report("Membuat suara pembaca", None)

    try:
        from piper import SynthesisConfig
        config = SynthesisConfig(length_scale=SPEED)
    except ImportError:
        config = None

    pitch = PIPER_PITCH[gender]
    raw_path = wav_path if pitch == 1.0 else wav_path + ".raw.wav"
    try:
        with wave.open(raw_path, "wb") as file:
            voice.synthesize_wav(spoken, file, syn_config=config)
        if pitch != 1.0:
            rate = _wav_rate(raw_path)
            _to_wav(raw_path, wav_path, (
                f"asetrate={int(rate * pitch)},aresample={rate},"
                f"atempo={1 / pitch:.4f}"
            ))
    finally:
        if raw_path != wav_path and os.path.exists(raw_path):
            os.remove(raw_path)


def synthesize_voice(text, wav_path, gender="wanita", report=None, voice=None):
    """Bacakan `text` ke file WAV dengan suara pria atau wanita.

    Mengembalikan {"seconds", "engine", "note"}. `engine` adalah "edge"
    atau "piper"; `note` menjelaskan bila Edge gagal lalu dipakai Piper.
    `voice` (objek Piper) memaksa Piper, dipakai untuk uji.
    """
    report = report or _ignore
    if gender not in GENDERS:
        raise ValueError(f"Suara tidak dikenal: {gender}")

    spoken = speech_text(text)
    if not spoken:
        raise ValueError("Teks hook kosong.")

    note = ""
    with _LOCK:
        use_edge = voice is None and ENGINE in ("auto", "edge") and edge_available()

        if use_edge:
            report("Membuat suara pembaca (Edge)", None)
            try:
                _edge_synthesize(spoken, wav_path, gender)
                return {"seconds": _wav_seconds(wav_path), "engine": "edge", "note": ""}
            except Exception as error:
                if ENGINE == "edge" or not piper_available():
                    raise RuntimeError(
                        "Suara pembaca Edge gagal dibuat (butuh internet). "
                        f"Keterangan: {str(error)[:200]}"
                    ) from error
                note = (
                    "Suara Edge gagal dibuat (mungkin tidak ada internet), "
                    "jadi dipakai suara offline Piper."
                )

        if voice is None and not piper_available():
            raise RuntimeError(
                "Suara pembaca belum terpasang. Jalankan ulang mulai_windows.bat."
            )

        _piper_synthesize(spoken, wav_path, gender, report, voice=voice)

    return {"seconds": _wav_seconds(wav_path), "engine": "piper", "note": note}


def synthesize(text, wav_path, report=None, voice=None, gender="wanita"):
    """Bacakan `text` ke file WAV. Mengembalikan durasi suara (detik)."""
    return synthesize_voice(
        text, wav_path, gender=gender, report=report, voice=voice
    )["seconds"]

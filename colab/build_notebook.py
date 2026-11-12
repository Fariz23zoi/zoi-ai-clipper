"""Membuat colab/zoi_transkrip_colab.ipynb dari potongan kode di bawah.

Jalankan ulang file ini bila isi notebook diubah:  python colab/build_notebook.py
"""
import json
import os

INTRO = """# ZOI AI CLIPPER: transkrip lewat Google Colab

Notebook ini membuat transkrip (file **.srt**) dari audio video, memakai GPU gratis Google. Hasilnya diimpor ke ZOI lewat halaman **Transkrip lewat Google Colab**.

**Langkah:**
1. Pilih **Runtime → Change runtime type → T4 GPU**, lalu **Save**.
2. Pilih **Runtime → Run all**.
3. Saat muncul tombol **Choose Files** di bawah, pilih file audio yang diunduh dari ZOI.
4. Tunggu sampai file `.srt` terunduh otomatis, lalu impor ke ZOI.

Tidak perlu API key. GPU gratis Colab dibatasi waktu pemakaian; bila sedang tidak tersedia, notebook tetap jalan di CPU tetapi jauh lebih lambat."""

SETUP = """# 1. Pasang pengenal suara (sekitar 1 menit)
!pip install -q faster-whisper
import ctranslate2
if ctranslate2.get_cuda_device_count():
    print("GPU aktif. Siap.")
else:
    print("PERINGATAN: GPU tidak aktif. Pilih Runtime -> Change runtime type -> T4 GPU,")
    print("lalu Runtime -> Run all lagi. Tanpa GPU prosesnya jauh lebih lambat.")"""

UPLOAD = """# 2. Pilih file audio dari ZOI
from google.colab import files
uploaded = files.upload()
AUDIO = next(iter(uploaded))
print("File:", AUDIO)"""

CORE = """# 3. Buat transkrip
BAHASA = "id"     # Bahasa ucapan. Ganti "auto" untuk deteksi otomatis.
MODEL = "turbo"   # Whisper large-v3-turbo: akurat dan cepat di GPU.

import subprocess, time
import numpy
import ctranslate2
from faster_whisper import WhisperModel, BatchedInferencePipeline


def baca_audio(path):
    # Dibaca lewat FFmpeg: decode_audio milik faster-whisper bermasalah
    # dengan pustaka av versi baru.
    hasil = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1",
         "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True)
    return numpy.frombuffer(hasil.stdout, dtype=numpy.float32)


def jam(detik):
    ms = int(round(detik * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def kelompokkan(kata, maks_detik=6.0):
    # Kata bertimestamp -> baris subtitle pendek, supaya ZOI bisa memotong
    # klip tepat di batas kalimat.
    baris, isi = [], []
    for k in kata:
        if isi and (k["start"] - isi[-1]["end"] > 1.5
                    or k["end"] - isi[0]["start"] > maks_detik):
            baris.append(isi)
            isi = []
        isi.append(k)
        if k["text"][-1:] in ".?!" and k["end"] - isi[0]["start"] >= 2:
            baris.append(isi)
            isi = []
    if isi:
        baris.append(isi)
    return [(b[0]["start"], max(b[-1]["end"], b[0]["start"] + 0.2),
             " ".join(k["text"] for k in b)) for b in baris]


def transkrip(path, model_name=MODEL, bahasa=BAHASA, device=None):
    gpu = ctranslate2.get_cuda_device_count() > 0
    device = device or ("cuda" if gpu else "cpu")
    model = WhisperModel(model_name, device=device,
                         compute_type="float16" if device == "cuda" else "int8")
    pipeline = BatchedInferencePipeline(model=model)
    audio = baca_audio(path)
    total = len(audio) / 16000
    mulai = time.time()
    segmen, info = pipeline.transcribe(
        audio, language=None if bahasa == "auto" else bahasa,
        batch_size=16 if device == "cuda" else 8, beam_size=1,
        word_timestamps=True)
    kata = []
    for s in segmen:
        for w in s.words or []:
            teks = w.word.strip()
            if not teks:
                continue
            if kata and (teks.startswith("-") or (teks[0] in ".," and teks[1:2].isdigit())):
                kata[-1]["text"] += teks
                kata[-1]["end"] = max(kata[-1]["end"], w.end)
            else:
                kata.append({"start": w.start, "end": w.end, "text": teks})
        print(f"\\r{s.end / 60:5.1f} dari {total / 60:.1f} menit", end="")
    print(f"\\nSelesai dalam {time.time() - mulai:.0f} detik. Bahasa: {info.language}")
    return kelompokkan(kata)


def tulis_srt(baris, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, b, teks) in enumerate(baris, 1):
            f.write(f"{i}\\n{jam(a)} --> {jam(b)}\\n{teks}\\n\\n")
    return path


if __name__ == "__main__" and "AUDIO" in globals():
    SRT = tulis_srt(transkrip(AUDIO), AUDIO.rsplit(".", 1)[0] + ".srt")
    print("File transkrip:", SRT)"""

DOWNLOAD = """# 4. Unduh transkrip ke laptop, lalu impor ke ZOI
files.download(SRT)"""


def code(source):
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": source.splitlines(keepends=True)}


notebook = {
    "nbformat": 4, "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": []}, "accelerator": "GPU",
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
    },
    "cells": [
        {"cell_type": "markdown", "metadata": {},
         "source": INTRO.splitlines(keepends=True)},
        code(SETUP), code(UPLOAD), code(CORE), code(DOWNLOAD),
    ],
}

target = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "zoi_transkrip_colab.ipynb")
with open(target, "w", encoding="utf-8") as file:
    json.dump(notebook, file, ensure_ascii=False, indent=1)
    file.write("\n")
print("Ditulis:", target)

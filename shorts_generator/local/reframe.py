"""Bingkai otomatis: ikuti pembicara, atau kembali ke 16:9 penuh.

Video sumber diperiksa dua kali per detik pada ukuran kecil (lebar 320
piksel) dengan pendeteksi wajah OpenCV. Hasilnya dibagi per shot
(pergantian kamera dikenali dari perubahan gambar yang besar):

* satu wajah (atau beberapa yang berdekatan) -> bingkai 1:1, digeser ke sana;
* wajah-wajah tersebar lebih lebar dari potongan 1:1 -> 16:9 penuh
  (kamera lebar, semua orang tetap terlihat);
* tidak ada wajah -> 16:9 penuh.

Posisi potongan tetap selama satu shot (tidak bergoyang). Shot yang
sangat pendek tanpa pembicara mengikuti shot sebelumnya supaya bingkai
tidak berkedip; batas antarshot dicari sampai ketelitian satu gambar.

Opsional: butuh paket opencv-python-headless (requirements-reframe.txt).
Tanpa paket itu, plan_auto_frame() mengembalikan None.
"""
import importlib.util
import os
import subprocess
from typing import List, Optional

SAMPLE_FPS = 2.0
SAMPLE_WIDTH = 320
# Rata-rata selisih piksel (0-255) di atas angka ini dianggap ganti shot.
CUT_THRESHOLD = 28.0
# Shot lebih pendek dari ini mengikuti shot sebelumnya.
MIN_SHOT = 1.2
# Wajah lebih kecil dari ini (bagian dari tinggi gambar) diabaikan.
MIN_FACE = 0.08
# Wajah kedua dihitung bila luasnya minimal sebesar ini dibanding wajah terbesar.
SECOND_FACE = 0.35
# Sebagian besar sampel shot harus satu wajah agar dipotong 1:1.
SINGLE_SHARE = 0.5
SAME_X = 0.06


def available() -> bool:
    """True bila OpenCV dengan pendeteksi wajah Haar terpasang.

    OpenCV 5 memindahkan pendeteksi Haar ke paket lain, jadi yang dipakai
    OpenCV 4 (lihat requirements-reframe.txt).
    """
    if importlib.util.find_spec("cv2") is None:
        return False
    try:
        import cv2
    except Exception:
        return False
    return hasattr(cv2, "CascadeClassifier")


def _detector():
    import cv2
    path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(path)
    if cascade.empty():
        raise RuntimeError("Pendeteksi wajah OpenCV tidak bisa dimuat.")

    def detect(gray):
        return [tuple(int(v) for v in face) for face in cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5,
            minSize=(max(12, int(gray.shape[0] * MIN_FACE)),) * 2
        )]
    return detect


def _read_frames(source_path, start, duration, aspect, fps=SAMPLE_FPS):
    """Gambar abu-abu kecil dari klip, `fps` per detik."""
    import numpy as np

    width = SAMPLE_WIDTH
    height = max(2, int(round(width / max(aspect, 0.1) / 2)) * 2)
    command = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", source_path, "-an",
        "-vf", f"fps={fps},scale={width}:{height},format=gray",
        "-f", "rawvideo", "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(
            "Gambar video gagal dibaca untuk bingkai otomatis: "
            + result.stderr.decode("utf-8", errors="replace")[-300:]
        )
    size = width * height
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    count = len(data) // size
    return [data[i * size:(i + 1) * size].reshape(height, width) for i in range(count)]


def _distinct(faces):
    """Buang kotak yang pusatnya berada di dalam kotak lain yang lebih besar
    (pendeteksi kadang melaporkan wajah yang sama dua kali)."""
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    kept = []
    for x, y, w, h in faces:
        cx, cy = x + w / 2, y + h / 2
        if any(kx <= cx <= kx + kw and ky <= cy <= ky + kh for kx, ky, kw, kh in kept):
            continue
        kept.append((x, y, w, h))
    return kept


def _sample_view(faces, width, height):
    """("square", posisi 0-1) bila wajah-wajah muat dalam potongan 1:1,
    ("wide", None) bila tersebar lebih lebar, ("none", None) bila tak ada."""
    faces = _distinct([f for f in faces if f[3] >= height * MIN_FACE])
    if not faces:
        return "none", None
    biggest = faces[0][2] * faces[0][3]
    faces = [f for f in faces if f[2] * f[3] >= biggest * SECOND_FACE]
    side = min(width, height)
    left = min(f[0] for f in faces)
    right = max(f[0] + f[2] for f in faces)
    if len(faces) > 1 and right - left > side * 0.8:
        return "wide", None
    if width <= side:
        return "square", 0.5
    centre = (left + right) / 2
    return "square", max(0.0, min(1.0, (centre - side / 2) / (width - side)))


def _median(values):
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def plan_from_frames(frames, detect, step=1 / SAMPLE_FPS, duration=None):
    """Rencana bingkai dari gambar-gambar abu-abu (untuk uji dan render)."""
    if not frames:
        return []
    import numpy as np

    height, width = frames[0].shape
    duration = duration if duration is not None else len(frames) * step

    # 1. Bagi menjadi shot.
    shots = [[0]]
    for index in range(1, len(frames)):
        change = float(np.mean(np.abs(
            frames[index].astype(np.int16) - frames[index - 1].astype(np.int16)
        )))
        if change > CUT_THRESHOLD:
            shots.append([index])
        else:
            shots[-1].append(index)

    # 2. Keputusan per shot.
    plan = []
    for shot in shots:
        views = [_sample_view(detect(frames[i]), width, height) for i in shot]
        singles = [x for kind, x in views if kind == "square"]
        if singles and len(singles) / len(views) >= SINGLE_SHARE:
            view = {"frame": "square", "x": round(_median(singles), 3)}
        else:
            view = {"frame": "wide", "x": 0.5}
        start = shot[0] * step
        end = min(duration, (shot[-1] + 1) * step)
        plan.append({"start": round(start, 3), "end": round(end, 3), **view})

    # 3. Shot sangat pendek tanpa pembicara mengikuti shot sebelumnya (supaya
    #    tidak berkedip ke 16:9). Shot pendek yang punya pembicara sendiri
    #    tetap memakai posisinya: posisi shot lain bisa jatuh di bagian kosong.
    #    Bagian yang sama lalu digabung.
    merged = []
    for part in plan:
        if merged and part["end"] - part["start"] < MIN_SHOT and part["frame"] == "wide":
            merged[-1]["end"] = part["end"]
            continue
        if merged and merged[-1]["frame"] == part["frame"] and (
            part["frame"] == "wide" or abs(merged[-1]["x"] - part["x"]) <= SAME_X
        ):
            merged[-1]["end"] = part["end"]
            continue
        merged.append(dict(part))
    if merged:
        merged[0]["start"] = 0.0
        merged[-1]["end"] = round(duration, 3)
    return merged


def _read_frames_with_times(source_path, start, duration, aspect):
    """Semua gambar asli (tanpa diubah fps-nya) beserta waktunya, detik
    sejak `start`. Waktu diambil dari FFmpeg (showinfo), jadi tepat."""
    import re
    import numpy as np

    width = SAMPLE_WIDTH
    height = max(2, int(round(width / max(aspect, 0.1) / 2)) * 2)
    command = [
        "ffmpeg", "-v", "info", "-nostats", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", source_path, "-an",
        "-vf", f"scale={width}:{height},format=gray,showinfo",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=120)
    if result.returncode:
        return [], []
    times = [float(t) for t in re.findall(
        rb"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:(-?[\d.]+)", result.stderr
    )]
    size = width * height
    count = min(len(times), len(result.stdout) // size)
    frames = [
        np.frombuffer(result.stdout[i * size:(i + 1) * size], dtype=np.uint8).reshape(height, width)
        for i in range(count)
    ]
    return frames, times[:count]


def _biggest_change(frames):
    """Indeks gambar dengan perubahan terbesar dari gambar sebelumnya."""
    import numpy as np
    best, index = -1.0, None
    for i in range(1, len(frames)):
        change = float(np.mean(np.abs(
            frames[i].astype(np.int16) - frames[i - 1].astype(np.int16)
        )))
        if change > best:
            best, index = change, i
    return index, best


def refine_cuts(plan, source_path, start, aspect, step=1 / SAMPLE_FPS):
    """Geser batas antarbagian ke gambar ganti kamera yang sebenarnya.

    Pemeriksaan dua kali per detik hanya tahu ganti kamera terjadi di
    antara dua sampel (filter fps FFmpeg memberi tiap sampel gambar
    terakhir di slotnya, jadi kira-kira antara b - step/2 dan b + step/2).
    Di jendela itu semua gambar asli dibaca beserta waktunya, lalu batas
    diletakkan tepat di antara gambar terakhir shot lama dan gambar
    pertama shot baru.
    """
    for before, after in zip(plan, plan[1:]):
        boundary = after["start"]
        window_start = max(0.0, boundary - step / 2 - 0.1)
        window_end = boundary + step / 2 + 0.1
        frames, times = _read_frames_with_times(
            source_path, start + window_start, window_end - window_start, aspect
        )
        index, change = _biggest_change(frames)
        if index is None or change <= CUT_THRESHOLD:
            continue
        exact = round(window_start + (times[index - 1] + times[index]) / 2, 3)
        if before["start"] < exact < after["end"]:
            before["end"] = after["start"] = exact
    return plan


def plan_auto_frame(source_path, start, end, aspect, report=None) -> Optional[List[dict]]:
    """Rencana bingkai otomatis untuk satu klip, waktu relatif awal klip.

    Mengembalikan daftar {"start", "end", "frame": "square"/"wide", "x"},
    atau None bila OpenCV tidak terpasang.
    """
    if not available():
        return None
    if report:
        report(2, "Mencari pembicara untuk bingkai otomatis")
    duration = max(0.1, end - start)
    frames = _read_frames(source_path, start, duration, aspect)
    plan = plan_from_frames(frames, _detector(), duration=duration)
    return refine_cuts(plan, source_path, start, aspect)


def describe(plan) -> str:
    """Ringkasan singkat untuk ditampilkan setelah render."""
    if not plan:
        return ""
    square = sum(p["end"] - p["start"] for p in plan if p["frame"] == "square")
    total = sum(p["end"] - p["start"] for p in plan) or 1
    if square == 0:
        return "Bingkai otomatis: tidak ada satu pembicara yang jelas, jadi seluruh klip 16:9 penuh."
    if square >= total - 0.01:
        return "Bingkai otomatis: seluruh klip dipotong 1:1 mengikuti pembicara."
    return (
        f"Bingkai otomatis: {len(plan)} bagian, {round(square)} detik 1:1 mengikuti "
        f"pembicara dan {round(total - square)} detik 16:9 penuh."
    )

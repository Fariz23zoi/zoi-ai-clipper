"""Pertukaran analisis dengan AI eksternal lewat file JSON.

Tidak memakai API key dan tidak memanggil jaringan. Alurnya:

1. ZOI menyimpan transkrip dan mengekspor paket analisis (JSON + prompt).
2. Pengguna memberikan paket itu ke Claude (atau AI lain) secara manual.
3. AI mengembalikan JSON berisi kandidat klip.
4. ZOI membaca JSON itu, memvalidasi setiap kandidat terhadap transkrip,
   lalu menampilkan kandidat yang lolos untuk dipilih dan dirender.

Hanya memakai pustaka standar Python.
"""
import hashlib
import json
import os
import re
import time

from .config import LOCAL_OUTPUT_DIR

EXPORT_SCHEMA = "zoi.export.v1"
IMPORT_SCHEMA = "zoi.import.v1"

MIN_DURATION = 30.0
MAX_DURATION = 90.0
MAX_CANDIDATES = 10
MAX_OVERLAP = 0.40
MIN_WORDS = 20
SNAP_TOLERANCE = 3.0
SEGMENT_MISMATCH = 3.0
MAX_IMPORT_BYTES = 1_000_000

DATA_DIR = os.path.join(LOCAL_OUTPUT_DIR, "data")

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ExchangeError(ValueError):
    """Kesalahan yang pesannya aman ditampilkan ke pengguna."""


# ---------------------------------------------------------------- penyimpanan

def is_valid_video_id(video_id):
    return bool(_VIDEO_ID.match(str(video_id or "")))


def video_dir(video_id):
    if not is_valid_video_id(video_id):
        raise ExchangeError("ID video tidak valid.")
    return os.path.join(DATA_DIR, video_id)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp{os.getpid()}"

    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=1)

    os.replace(temp_path, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def save_transcript(transcript):
    path = os.path.join(video_dir(transcript.get("video_id")), "transcript.json")
    _write_json(path, transcript)
    return path


def load_transcript(video_id):
    return _read_json(os.path.join(video_dir(video_id), "transcript.json"))


def save_analysis(video_id, analysis):
    _write_json(os.path.join(video_dir(video_id), "analysis.json"), analysis)


def load_analysis(video_id):
    return _read_json(os.path.join(video_dir(video_id), "analysis.json"))


def list_videos():
    """Daftar video yang pernah dianalisis, terbaru lebih dulu."""
    videos = []

    try:
        names = os.listdir(DATA_DIR)
    except OSError:
        return videos

    for name in names:
        if not is_valid_video_id(name):
            continue

        analysis = load_analysis(name)
        if not analysis:
            continue

        videos.append({
            "video_id": name,
            "title": analysis.get("title") or name,
            "duration": analysis.get("duration", 0),
            "updated_at": analysis.get("updated_at", 0),
            "candidate_count": sum(
                len(group.get("clips", []))
                for group in analysis.get("groups", [])
            )
        })

    videos.sort(key=lambda item: item["updated_at"], reverse=True)
    return videos


# --------------------------------------------------------------------- ekspor

def _clean_segments(transcript):
    segments = []

    for seg in transcript.get("segments", []):
        text = str(seg.get("text", "")).strip()
        if not text:
            continue

        segments.append({
            "i": len(segments),
            "start": round(float(seg.get("start", 0)), 2),
            "end": round(float(seg.get("end", 0)), 2),
            "text": text
        })

    return segments


def transcript_hash(transcript):
    """Sidik jari transkrip, untuk mencocokkan hasil impor."""
    payload = json.dumps(
        [[s["start"], s["end"], s["text"]] for s in _clean_segments(transcript)],
        ensure_ascii=False,
        separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def transcript_duration(transcript):
    segments = transcript.get("segments", [])
    last_end = max((float(s.get("end", 0)) for s in segments), default=0.0)
    return float(transcript.get("duration") or last_end)


def build_export(transcript, zoi_candidates=None):
    """Susun paket analisis untuk AI eksternal."""
    segments = _clean_segments(transcript)

    if not segments:
        raise ExchangeError("Transkrip kosong, tidak ada yang bisa diekspor.")

    return {
        "schema": EXPORT_SCHEMA,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engine": "ZOI AI CLIPPER",
        "video": {
            "video_id": transcript.get("video_id", ""),
            "url": transcript.get("url", ""),
            "title": transcript.get("title", ""),
            "channel": transcript.get("channel", ""),
            "duration": round(transcript_duration(transcript), 2),
            "language": transcript.get("language", "")
        },
        "constraints": {
            "time_unit": "seconds",
            "min_duration": MIN_DURATION,
            "max_duration": MAX_DURATION,
            "max_candidates": MAX_CANDIDATES
        },
        "transcript": {
            "sha256": transcript_hash(transcript),
            "segment_count": len(segments),
            "segments": segments
        },
        "zoi_candidates": [
            {
                "title": item.get("title", ""),
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
                "score": item.get("score", 0)
            }
            for item in (zoi_candidates or [])
        ]
    }


def build_prompt(export, num_clips=5):
    """Teks siap tempel untuk Claude: instruksi + data transkrip."""
    video = export["video"]
    limits = export["constraints"]

    example = {
        "schema": IMPORT_SCHEMA,
        "video_id": video["video_id"],
        "transcript_sha256": export["transcript"]["sha256"],
        "analyst": "claude",
        "highlights": [
            {
                "title": "Judul singkat kandidat",
                "start_segment": 12,
                "end_segment": 18,
                "start_time": 96.4,
                "end_time": 151.2,
                "hook_sentence": "Kalimat pembuka, disalin persis dari transkrip",
                "virality_reason": "Alasan momen ini menarik",
                "score": 88
            }
        ]
    }

    data = json.dumps(export, ensure_ascii=False, separators=(",", ":"))

    return f"""Kamu adalah analis profesional video pendek (YouTube Shorts, TikTok, Reels).

TUGAS
Analisis transkrip video di bawah dan pilih {num_clips} momen terbaik untuk dijadikan video pendek vertikal.

Video: {video['title']}
Durasi: {video['duration']} detik

YANG DICARI
1. RELATE: pembuka yang langsung membuat penonton paham konteks dan penasaran.
2. KONFLIK: masalah, kejutan, emosi, perdebatan, atau pertanyaan yang menggantung.
3. PAYOFF: jawaban, hasil, atau reaksi yang memuaskan di akhir klip.
4. Klip harus bisa dipahami sendiri tanpa menonton bagian lain.
Nilai isi dan alur ceritanya, bukan sekadar adanya kata yang heboh.

ATURAN TIMESTAMP
- Semua waktu dalam detik.
- Durasi tiap kandidat {int(limits['min_duration'])} sampai {int(limits['max_duration'])} detik.
- start_time harus sama dengan "start" milik segmen start_segment.
- end_time harus sama dengan "end" milik segmen end_segment.
- Mulai dan akhiri di batas kalimat, jangan memotong ucapan.
- Kandidat tidak boleh saling tumpang tindih.
- Jangan mengarang dialog. hook_sentence harus disalin persis dari transkrip.

FORMAT JAWABAN
Jawab dengan JSON valid saja, tanpa penjelasan dan tanpa teks lain.
Salin video_id dan transcript_sha256 persis seperti contoh ini:

{json.dumps(example, ensure_ascii=False, indent=2)}

DATA TRANSKRIP (JSON)
{data}
"""


# ---------------------------------------------------------------------- impor

def parse_import(raw_text):
    """Ambil objek JSON dari teks jawaban AI.

    Menerima JSON murni, JSON di dalam pagar kode markdown, maupun JSON
    yang diapit kalimat pembuka dan penutup.
    """
    if isinstance(raw_text, bytes):
        raw_text = raw_text.decode("utf-8", errors="replace")

    text = str(raw_text or "").strip().lstrip("﻿")

    if not text:
        raise ExchangeError("Tidak ada teks JSON yang diberikan.")

    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ExchangeError("File impor terlalu besar (batas 1 MB).")

    candidates = [text]

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())

    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])

    first, last = text.find("["), text.rfind("]")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue

        if isinstance(data, list):
            data = {"highlights": data}

        if isinstance(data, dict):
            return data

    raise ExchangeError(
        "Teks yang ditempel bukan JSON yang valid. Salin seluruh jawaban "
        "JSON dari Claude, mulai dari tanda { sampai tanda } terakhir."
    )


def _to_seconds(value):
    """Terima angka detik atau teks 'MM:SS' / 'HH:MM:SS'."""
    if isinstance(value, bool) or value is None:
        raise ValueError("kosong")

    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().replace(",", ".")

        if ":" in text:
            parts = text.split(":")
            if len(parts) > 3:
                raise ValueError("format waktu")
            seconds = sum(
                float(part) * (60 ** index)
                for index, part in enumerate(reversed(parts))
            )
        else:
            seconds = float(text)

    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        raise ValueError("bukan angka")

    return seconds


def _to_score(value):
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _to_index(value, count):
    if isinstance(value, bool) or value is None:
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < count else None


def _normalize_text(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _snap(value, points):
    """Geser ke titik terdekat bila selisihnya masih dalam toleransi."""
    nearest = min(points, key=lambda point: abs(point - value))
    if abs(nearest - value) <= SNAP_TOLERANCE:
        return nearest
    return value


def overlap_ratio(first, second):
    """Bagian tumpang tindih dibanding klip yang lebih pendek (0..1)."""
    overlap = max(
        0.0,
        min(first["end_time"], second["end_time"])
        - max(first["start_time"], second["start_time"])
    )
    shorter = min(
        first["end_time"] - first["start_time"],
        second["end_time"] - second["start_time"]
    )
    return overlap / shorter if shorter > 0 else 0.0


def format_time(seconds):
    seconds = int(float(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def validate_import(data, transcript, max_candidates=MAX_CANDIDATES):
    """Periksa hasil analisis AI terhadap transkrip.

    Mengembalikan dict:
      accepted       kandidat yang lolos, formatnya sama dengan ZOI Engine
      rejected       kandidat yang ditolak beserta alasannya
      file_warnings  peringatan tingkat file
      analyst        nama analis dari file
    Melempar ExchangeError bila file sama sekali tidak bisa dipakai.
    """
    if not isinstance(data, dict):
        raise ExchangeError("Isi JSON harus berupa objek.")

    schema = data.get("schema")
    if schema is not None and not str(schema).startswith("zoi.import"):
        raise ExchangeError(
            f"Format file tidak dikenal (schema: {str(schema)[:40]}). "
            "Yang diterima adalah zoi.import.v1."
        )

    highlights = data.get("highlights")
    if not isinstance(highlights, list):
        raise ExchangeError('JSON tidak memiliki daftar "highlights".')

    if not highlights:
        raise ExchangeError('Daftar "highlights" kosong.')

    segments = _clean_segments(transcript)
    if not segments:
        raise ExchangeError("Transkrip video ini kosong.")

    video_id = transcript.get("video_id", "")
    duration = transcript_duration(transcript)
    file_warnings = []

    imported_id = data.get("video_id")
    if imported_id is None or str(imported_id).strip() == "":
        file_warnings.append(
            "File tidak menyebutkan video_id, jadi tidak bisa dipastikan "
            "analisis ini dibuat untuk video ini."
        )
    elif str(imported_id).strip() != video_id:
        raise ExchangeError(
            f"Analisis ini dibuat untuk video lain ({str(imported_id)[:40]}), "
            f"bukan untuk video ini ({video_id})."
        )

    imported_hash = data.get("transcript_sha256")
    if imported_hash and str(imported_hash).strip() != transcript_hash(transcript):
        file_warnings.append(
            "Analisis dibuat dari versi transkrip yang berbeda. "
            "Timestamp tetap diperiksa, tetapi sebaiknya ekspor ulang."
        )

    starts = [s["start"] for s in segments]
    ends = [s["end"] for s in segments]

    valid = []
    rejected = []

    def reject(index, item, reason):
        title = ""
        if isinstance(item, dict):
            title = str(item.get("title", ""))[:100]
        rejected.append({
            "index": index + 1,
            "title": title or f"Kandidat {index + 1}",
            "reason": reason
        })

    for index, item in enumerate(highlights):
        if not isinstance(item, dict):
            reject(index, item, "bukan objek JSON")
            continue

        warnings = []
        start_segment = _to_index(item.get("start_segment"), len(segments))
        end_segment = _to_index(item.get("end_segment"), len(segments))

        try:
            start = _to_seconds(item.get("start_time"))
        except (TypeError, ValueError):
            start = None
        try:
            end = _to_seconds(item.get("end_time"))
        except (TypeError, ValueError):
            end = None

        # Nomor segmen adalah jangkar yang bisa diperiksa. Bila waktunya
        # tidak cocok dengan segmen itu, timestamp kemungkinan dikarang.
        if start_segment is not None:
            anchor = segments[start_segment]["start"]
            if start is None:
                start = anchor
            elif abs(start - anchor) > SEGMENT_MISMATCH:
                reject(
                    index, item,
                    f"start_time {start:.1f} tidak cocok dengan segmen "
                    f"{start_segment} (mulai {anchor:.1f})"
                )
                continue
            else:
                start = anchor

        if end_segment is not None:
            anchor = segments[end_segment]["end"]
            if end is None:
                end = anchor
            elif abs(end - anchor) > SEGMENT_MISMATCH:
                reject(
                    index, item,
                    f"end_time {end:.1f} tidak cocok dengan segmen "
                    f"{end_segment} (selesai {anchor:.1f})"
                )
                continue
            else:
                end = anchor

        if start is None or end is None:
            reject(index, item, "start_time atau end_time tidak ada atau bukan angka")
            continue

        if start < 0:
            reject(index, item, f"start_time negatif ({start:.1f})")
            continue

        if end > duration + 1:
            reject(
                index, item,
                f"end_time {end:.1f} melewati durasi video ({duration:.1f} detik)"
            )
            continue

        if end <= start:
            reject(index, item, "end_time tidak lebih besar dari start_time")
            continue

        snapped_start = _snap(start, starts)
        snapped_end = _snap(end, ends)

        if snapped_end > snapped_start:
            if abs(snapped_start - start) > 0.05 or abs(snapped_end - end) > 0.05:
                warnings.append("Waktu digeser ke batas kalimat terdekat.")
            start, end = snapped_start, snapped_end

        end = min(end, duration) if duration > start else end
        clip_duration = end - start

        if clip_duration < MIN_DURATION or clip_duration > MAX_DURATION:
            reject(
                index, item,
                f"durasi {clip_duration:.0f} detik, batasnya "
                f"{int(MIN_DURATION)}-{int(MAX_DURATION)} detik"
            )
            continue

        inside = [
            s for s in segments
            if s["end"] > start and s["start"] < end
        ]
        clip_text = " ".join(s["text"] for s in inside)

        if len(clip_text.split()) < MIN_WORDS:
            reject(
                index, item,
                "hampir tidak ada ucapan pada rentang waktu itu"
            )
            continue

        hook = str(item.get("hook_sentence", "") or "").strip()[:250]
        if hook:
            hook_words = _normalize_text(hook).split()
            probe = " ".join(hook_words[:6])
            if probe and probe not in _normalize_text(clip_text):
                warnings.append(
                    "Kalimat hook tidak ditemukan di transkrip pada rentang ini."
                )

        valid.append({
            "title": (
                str(item.get("title", "") or "").strip()[:100]
                or f"Kandidat {index + 1}"
            ),
            "start_time": round(start, 2),
            "end_time": round(end, 2),
            "duration": round(clip_duration, 2),
            "score": _to_score(item.get("score", 0)),
            "hook_sentence": hook,
            "virality_reason": str(
                item.get("virality_reason", "") or ""
            ).strip()[:500],
            "warnings": warnings,
            "_index": index
        })

    valid.sort(key=lambda item: item["score"], reverse=True)

    accepted = []
    for item in valid:
        clash = next(
            (old for old in accepted if overlap_ratio(item, old) >= MAX_OVERLAP),
            None
        )

        if clash:
            reject(
                item["_index"], item,
                f"tumpang tindih dengan kandidat \"{clash['title'][:40]}\""
            )
            continue

        if len(accepted) >= max_candidates:
            reject(
                item["_index"], item,
                f"melebihi batas {max_candidates} kandidat"
            )
            continue

        accepted.append(item)

    for item in accepted:
        del item["_index"]

    rejected.sort(key=lambda entry: entry["index"])

    return {
        "accepted": accepted,
        "rejected": rejected,
        "file_warnings": file_warnings,
        "analyst": str(data.get("analyst", "") or "").strip()[:40]
    }

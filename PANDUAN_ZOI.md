# Panduan ZOI AI CLIPPER

Dashboard lokal untuk membuat video Shorts 9:16 dari video YouTube, berjalan di Android Termux tanpa API key.

## Memasang

```bash
pkg install python ffmpeg nodejs git
pip install -r requirements-termux.txt
```

## Menjalankan

```bash
cd ~/projects/ZOI-AI-CLIPPER
python app.py
```

Buka `http://127.0.0.1:5000` di browser HP. Untuk membuka dari perangkat lain di Wi-Fi yang sama, jalankan `ZOI_HOST=0.0.0.0 python app.py`.

## Alur pakai

1. Tempel link YouTube, pilih mode, tekan **Analisis video**. Video harus punya subtitle/CC bahasa Indonesia atau Inggris.
2. Halaman progres menampilkan tahap: subtitle, analisis, unduhan (dengan persentase nyata dari yt-dlp).
3. Halaman kandidat menampilkan hasil **ZOI Engine V4.1** dan **V4.2 (eksperimental)**.
4. Tekan **Render klip ini** pada kandidat pilihan. Persentase berasal dari progres FFmpeg.
5. Putar atau unduh hasilnya. File tersimpan di `output/` dengan awalan `zoi_`.

## Analisis dengan Claude tanpa API key

Di halaman kandidat, buka bagian **Analisis AI eksternal**:

1. Tekan **Salin prompt** (atau unduh `.txt`).
2. Tempel ke Claude dan tunggu jawaban JSON.
3. Tempel jawabannya ke kotak **Jawaban JSON**, lalu tekan **Impor dan validasi**.

ZOI menolak kandidat yang timestamp-nya di luar durasi video, durasinya di luar 30–90 detik, tidak cocok dengan nomor segmen, hampir tanpa ucapan, atau tumpang tindih 40% ke atas dengan kandidat lain. Maksimal 10 kandidat. Kandidat yang ditolak ditampilkan beserta alasannya.

Format jawaban yang diterima:

```json
{
  "schema": "zoi.import.v1",
  "video_id": "ID_VIDEO",
  "transcript_sha256": "disalin dari prompt",
  "analyst": "claude",
  "highlights": [
    {
      "title": "Judul singkat",
      "start_segment": 12,
      "end_segment": 18,
      "start_time": 96.4,
      "end_time": 151.2,
      "hook_sentence": "Kalimat persis dari transkrip",
      "virality_reason": "Alasan",
      "score": 88
    }
  ]
}
```

## Mode

| Mode | Tanpa API key | Dengan API key di `.env` |
|---|---|---|
| ZOI | ZOI Engine saja | sama |
| Claude / ChatGPT / Hybrid | ZOI Engine + panduan ekspor/impor | ZOI Engine + analisis lewat API |

## Tempat file

| Lokasi | Isi |
|---|---|
| `output/source_<id>.mp4` | Video sumber (tidak pernah dihapus aplikasi) |
| `output/zoi_<id>_<mulai>-<selesai>_<judul>_<acak>.mp4` | Hasil render |
| `output/data/<id>/transcript.json` | Transkrip tersimpan |
| `output/data/<id>/analysis.json` | Kandidat ZOI dan hasil impor |
| `backups/` | Salinan file sebelum perubahan besar |

## Menguji

```bash
python -m compileall -q app.py shorts_generator tests
python -m unittest -v
```

Tes memakai video buatan FFmpeg dan transkrip contoh, sehingga tidak butuh internet. Unduhan YouTube sungguhan perlu dicoba manual lewat dashboard.

## Pengaturan opsional (`.env`)

| Nama | Bawaan | Guna |
|---|---|---|
| `ZOI_HOST` | `127.0.0.1` | Alamat dashboard |
| `ZOI_PORT` | `5000` | Port dashboard |
| `ZOI_RENDER_PRESET` | `veryfast` | `ultrafast` lebih cepat, file lebih besar |
| `ZOI_RENDER_CRF` | `23` | Lebih kecil = kualitas lebih tinggi |
| `LOCAL_OUTPUT_DIR` | `output` | Folder hasil |

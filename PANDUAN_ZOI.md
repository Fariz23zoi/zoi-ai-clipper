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
2. Analisis hanya mengambil subtitle, jadi cepat dan hemat kuota. Video belum diunduh pada tahap ini.
3. Halaman kandidat menampilkan hasil **ZOI Engine V4.1** dan **V4.2 (eksperimental)**.
4. Tekan **Render klip ini** pada kandidat pilihan. Pada render pertama, video diunduh dulu (Langkah 1 dari 2, persentase dari yt-dlp), lalu dirender (Langkah 2 dari 2, persentase dari FFmpeg). Render berikutnya dari video yang sama langsung jalan.
5. Putar atau unduh hasilnya. File tersimpan di `output/` dengan awalan `zoi_`.

## Supaya ringan di HP

- **Kualitas video sumber.** Pilihan bawaan adalah Ringan (480p). Hasil akhir tetap 720x1280: bagian video di tengah selebar 720 piksel, sedangkan sumber 480p selebar 854 piksel, jadi masih cukup. Pilih Tajam (720p) bila ingin gambar lebih tajam dan HP sanggup.
- **Unduhan dipilih yang ringan.** Aplikasi meminta video paling banyak 30 fps dengan codec H.264.
- **Render dibatasi 30 fps.** Video sumber 50/60 fps dirender separuhnya (25/30 fps).
- **FFmpeg berjalan dengan prioritas rendah**, supaya HP tetap bisa dipakai selama rendering.
- **Kosongkan penyimpanan.** Di halaman video ada tombol **Hapus video sumber**. Kandidat tetap tersimpan, dan video diunduh lagi bila Anda merender. Klip hasil render juga bisa dihapus dari daftar.
- **Saran pemakaian.** Render satu klip pada satu waktu (aplikasi sudah mengantrekannya), tutup aplikasi lain yang berat, dan jalankan `termux-wake-lock` sebelum `python app.py` agar Android tidak menghentikan Termux saat layar mati.

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
| `output/source_<id>.mp4` | Video sumber (hanya terhapus bila Anda menekan Hapus video sumber) |
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
| `ZOI_RENDER_MAX_FPS` | `30` | Batas fps hasil render; `0` = ikut sumber |
| `ZOI_RENDER_NICE` | `10` | Prioritas FFmpeg (0-19); `0` = prioritas normal |
| `ZOI_DOWNLOAD_HEIGHT` | `480` | Kualitas sumber bawaan: `480` atau `720` |
| `ZOI_DOWNLOAD_AT_ANALYSIS` | kosong | Isi `1` bila video ingin langsung diunduh saat analisis |
| `LOCAL_OUTPUT_DIR` | `output` | Folder hasil |

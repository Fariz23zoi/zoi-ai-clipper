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

Dashboard terdiri dari tiga halaman.

**Halaman 1: tempel link.** Tempel link YouTube, pilih mode, tekan **Analisis video**. Video harus punya subtitle/CC bahasa Indonesia atau Inggris. Analisis hanya mengambil subtitle, jadi cepat dan hemat kuota; video belum diunduh.

**Halaman 2: analisis AI.** Hanya muncul pada mode Claude, ChatGPT, dan Hybrid. Salin prompt, tempel ke Claude atau ChatGPT, lalu tempel jawabannya dan tekan **Impor dan lihat hasil**. Mode ZOI melewati halaman ini.

**Halaman 3: pilih klip.** Pilih **Kualitas render**, lalu tekan **Render klip ini** pada kandidat pilihan. Hasil analisis AI tampil paling atas, hasil ZOI Engine V4.1 dan V4.2 di bawahnya. Pada render pertama, video diunduh dulu (Langkah 1 dari 2, persentase dari yt-dlp), lalu dirender (Langkah 2 dari 2, persentase dari FFmpeg). Hasilnya bisa diputar dan diunduh; file tersimpan di `output/` dengan awalan `zoi_`.

## Kualitas render

| Pilihan | Ukuran hasil | Video sumber yang diunduh |
|---|---|---|
| 240p | 240x426 | 144p |
| 360p | 360x640 | 240p |
| 480p | 480x854 | 360p |
| 720p | 720x1280 | 480p |
| 1080p | 1080x1920 | 720p |

- Kolom terakhir berlaku untuk video mendatar 16:9. Aplikasi mengunduh sumber paling ringan yang masih mengisi kanvas tanpa diperbesar. Contoh: kanvas selebar 720 piksel cukup diisi video 480p, karena video 480p lebarnya 854 piksel. Untuk video 4:3 atau video tegak, aplikasi otomatis meminta sumber yang lebih tinggi.
- Makin tinggi pilihannya, makin lama unduhan dan render.
- Video sumber disimpan satu per video. Bila Anda memilih kualitas yang lebih tinggi daripada sumber yang tersimpan, video diunduh ulang dengan kualitas yang cukup. Memilih kualitas lebih rendah memakai sumber yang sudah ada.
- Pilihan terakhir diingat browser. Bawaan pertama kali adalah 720p (bisa diubah lewat `ZOI_RENDER_QUALITY`).

## Supaya ringan di HP

- **Pilih kualitas render yang lebih rendah** bila HP terasa berat. 480p jauh lebih cepat daripada 720p atau 1080p.
- **Unduhan dipilih yang ringan.** Aplikasi meminta video paling banyak 30 fps dengan codec H.264.
- **Render dibatasi 30 fps.** Video sumber 50/60 fps dirender separuhnya (25/30 fps).
- **FFmpeg berjalan dengan prioritas rendah**, supaya HP tetap bisa dipakai selama rendering.
- **Kosongkan penyimpanan.** Di halaman 3 ada tombol **Hapus video sumber**. Kandidat tetap tersimpan, dan video diunduh lagi bila Anda merender. Klip hasil render juga bisa dihapus dari daftar.
- **Saran pemakaian.** Render satu klip pada satu waktu (aplikasi sudah mengantrekannya), tutup aplikasi lain yang berat, dan jalankan `termux-wake-lock` sebelum `python app.py` agar Android tidak menghentikan Termux saat layar mati.

## Analisis dengan Claude tanpa API key

Pada mode Claude, ChatGPT, atau Hybrid, halaman 2 terbuka otomatis setelah analisis. Pada mode ZOI, halaman ini bisa dibuka lewat tombol **Analisis dengan Claude dan ChatGPT** di bagian bawah halaman 3.

1. Tekan **Salin prompt**.
2. Tekan **Buka Claude** (atau **Buka ChatGPT**), tempel prompt, kirim, lalu salin seluruh jawabannya.
3. Tempel jawabannya ke kotak **Jawaban Claude** (atau **Jawaban ChatGPT**), lalu tekan **Impor dan lihat hasil**.

Pada mode Hybrid ada dua kotak; isi salah satu atau keduanya. Bila transkrip terlalu panjang untuk ditempel, buka **Pilihan lain: pakai file** untuk mengunduh prompt dan mengunggah jawaban sebagai file.

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
| ZOI | Halaman 1 lalu langsung halaman 3 | sama |
| Claude | Halaman 2 meminta jawaban Claude | Analisis lewat API, langsung ke halaman 3 |
| ChatGPT | Halaman 2 meminta jawaban ChatGPT | Analisis lewat API, langsung ke halaman 3 |
| Hybrid | Halaman 2 meminta jawaban Claude dan ChatGPT | Analisis lewat API untuk yang punya key |

ZOI Engine selalu dijalankan pada semua mode; hasilnya tampil di bawah hasil AI.

## Tempat file

| Lokasi | Isi |
|---|---|
| `output/source_<id>.mp4` | Video sumber (hanya terhapus bila Anda menekan Hapus video sumber) |
| `output/zoi_<id>_<mulai>-<selesai>_<kualitas>p_<judul>_<acak>.mp4` | Hasil render |
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
| `ZOI_RENDER_QUALITY` | `720` | Kualitas render bawaan: `240`, `360`, `480`, `720`, atau `1080` |
| `ZOI_DOWNLOAD_AT_ANALYSIS` | kosong | Isi `1` bila video ingin langsung diunduh saat analisis |
| `LOCAL_OUTPUT_DIR` | `output` | Folder hasil |

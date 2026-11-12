# Panduan ZOI AI CLIPPER

Dashboard lokal untuk membuat video Shorts 9:16 dari video YouTube, tanpa API key. Bisa dijalankan di laptop (Windows, Linux, macOS) atau di Android Termux.

## Memasang di laptop Windows

Laptop lebih kuat daripada HP, jadi unduhan dan render jauh lebih cepat.

**1. Pasang empat program.** Buka **PowerShell** (tekan tombol Windows, ketik `PowerShell`, Enter), lalu jalankan satu per satu. Bila ditanya persetujuan, ketik `Y` lalu Enter.

```powershell
winget install -e --id Python.Python.3.13
winget install -e --id Git.Git
winget install -e --id Gyan.FFmpeg
winget install -e --id OpenJS.NodeJS.LTS
```

**2. Tutup PowerShell, lalu buka lagi.** Program yang baru dipasang baru dikenali setelah jendela dibuka ulang.

**3. Ambil ZOI AI CLIPPER.**

```powershell
cd $HOME
git clone -b claude/zoi-clipper-audit-vuj4yd https://github.com/Fariz23zoi/zoi-ai-clipper.git
```

Karena repo ini privat, akan muncul jendela login GitHub. Masuk dengan akun GitHub Anda.

**4. Jalankan.** Buka File Explorer, masuk ke folder `zoi-ai-clipper` di dalam folder pengguna Anda, lalu klik dua kali **`mulai_windows.bat`**. Pertama kali butuh beberapa menit untuk menyiapkan Python. Setelah itu browser terbuka sendiri di `http://127.0.0.1:5000`.

| File | Guna |
|---|---|
| `mulai_windows.bat` | Menjalankan dashboard di laptop |
| `mulai_windows_hp.bat` | Menjalankan dashboard supaya juga bisa dibuka dari HP |
| `perbarui_windows.bat` | Mengambil versi terbaru (tutup dulu jendela ZOI yang sedang jalan) |

Untuk berhenti, tutup jendela hitam ZOI atau tekan `Ctrl` + `C` di dalamnya.

### Membuka dashboard dari HP

1. Laptop dan HP harus tersambung ke Wi-Fi yang sama.
2. Klik dua kali `mulai_windows_hp.bat`.
3. Bila Windows menanyakan izin firewall untuk Python, pilih **Allow** untuk jaringan pribadi (Private).
4. Di jendela ZOI tertulis alamat seperti `http://192.168.1.23:5000`. Ketik alamat itu di browser HP.

Laptop yang mengunduh dan merender; HP hanya menjadi layar. Selama mode ini aktif, siapa pun di Wi-Fi yang sama bisa membuka dashboard, jadi pakai hanya di Wi-Fi rumah.

## Memasang di laptop Linux atau macOS

Pasang Python 3, Git, FFmpeg, dan Node.js lewat pengelola paket (misalnya `sudo apt install python3-venv git ffmpeg nodejs` atau `brew install python git ffmpeg node`), lalu:

```bash
git clone -b claude/zoi-clipper-audit-vuj4yd https://github.com/Fariz23zoi/zoi-ai-clipper.git
cd zoi-ai-clipper
bash mulai.sh
```

`ZOI_HOST=0.0.0.0 bash mulai.sh` membuat dashboard bisa dibuka dari HP. `bash perbarui.sh` mengambil versi terbaru.

## Memasang di Android Termux

```bash
pkg install python ffmpeg nodejs git
pip install -r requirements-termux.txt
cd ~/projects/ZOI-AI-CLIPPER
python app.py
```

Buka `http://127.0.0.1:5000` di browser HP.

## Alur pakai

Dashboard terdiri dari tiga halaman.

**Halaman 1: tempel link.** Tempel link YouTube, pilih mode, tekan **Analisis video**. Video harus punya subtitle/CC bahasa Indonesia atau Inggris. Analisis hanya mengambil subtitle, jadi cepat dan hemat kuota; video belum diunduh.

Subtitle yang dicoba, berurutan: bahasa Indonesia, bahasa Inggris, lalu subtitle bahasa asli video. Bila subtitle tidak ada atau gagal diunduh, ZOI di laptop mengenali ucapan langsung dari suara video (lihat **Video tanpa subtitle** di bawah).

**Halaman 2: analisis AI.** Hanya muncul pada mode Claude, ChatGPT, dan Hybrid. Salin prompt, tempel ke Claude atau ChatGPT, lalu tempel jawabannya dan tekan **Impor dan lihat hasil**. Mode ZOI melewati halaman ini.

**Halaman 3: pilih klip.** Pilih **Kualitas render**, lalu tekan **Render klip ini** pada kandidat pilihan. Hasil analisis AI tampil paling atas, hasil ZOI Engine V4.1 dan V4.2 di bawahnya. Pada render pertama, video diunduh dulu (Langkah 1 dari 2, persentase dari yt-dlp), lalu dirender (Langkah 2 dari 2, persentase dari FFmpeg). Hasilnya bisa diputar dan diunduh; file tersimpan di `output/` dengan awalan `zoi_`.

## Video tanpa subtitle

Bila YouTube tidak menyediakan subtitle, atau subtitlenya gagal diunduh, ZOI di laptop mengenali ucapan dari suara video dengan **Whisper** (pengenal suara yang berjalan di laptop sendiri, tanpa API key dan tanpa biaya).

- **Hanya di laptop.** Di HP (Termux) fitur ini tidak tersedia; video tanpa subtitle tetap gagal dengan pesan yang menjelaskannya.
- **Butuh Python 3.13** (atau 3.10 sampai 3.12). Untuk Python 3.14 ke atas, salah satu komponennya belum tersedia. Peluncur Windows otomatis memakai Python 3.13 bila terpasang.
- **Dipasang otomatis** oleh `mulai_windows.bat` atau `mulai.sh` (sekitar 200 MB). Manual: `pip install -r requirements-speech.txt`.
- **Model diunduh sekali** saat pertama dipakai, sekitar 460 MB, ke folder `models/` di dalam folder ZOI.
- **Yang diunduh hanya audionya**, lalu dihapus setelah transkrip jadi. Bila video sumber sudah ada di laptop, video itu yang dipakai.
- **Butuh waktu.** Halaman analisis menampilkan kemajuannya, misalnya "Mengenali ucapan: 05:12 dari 27:04". Transkripnya disimpan, jadi menganalisis ulang video yang sama tidak mengulang proses ini.
- **Bisa ada salah dengar.** Halaman kandidat memberi tanda bila transkrip berasal dari suara. Tonton klipnya sebelum diunggah.
- **Bahasa dideteksi dari suara.** Bahasa Melayu, Jawa, dan Sunda ditulis sebagai bahasa Indonesia, karena Whisper sering tertukar di antara keempatnya.


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
| `models/` | Model pengenal suara (boleh dihapus; diunduh lagi bila perlu) |
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
| `ZOI_HOST` | `127.0.0.1` | Alamat dashboard; `0.0.0.0` agar bisa dibuka dari HP di Wi-Fi yang sama |
| `ZOI_OPEN_BROWSER` | kosong | Isi `1` agar browser terbuka sendiri saat aplikasi mulai |
| `ZOI_PORT` | `5000` | Port dashboard |
| `ZOI_RENDER_PRESET` | `veryfast` | `ultrafast` lebih cepat, file lebih besar |
| `ZOI_RENDER_CRF` | `23` | Lebih kecil = kualitas lebih tinggi |
| `ZOI_RENDER_MAX_FPS` | `30` | Batas fps hasil render; `0` = ikut sumber |
| `ZOI_RENDER_NICE` | `10` | Prioritas FFmpeg (0-19); `0` = prioritas normal |
| `ZOI_RENDER_QUALITY` | `720` | Kualitas render bawaan: `240`, `360`, `480`, `720`, atau `1080` |
| `ZOI_DOWNLOAD_AT_ANALYSIS` | kosong | Isi `1` bila video ingin langsung diunduh saat analisis |
| `LOCAL_OUTPUT_DIR` | `output` | Folder hasil |
| `ZOI_SPEECH` | `1` | Isi `0` untuk mematikan pengenal suara |
| `ZOI_WHISPER_MODEL` | `small` | `base` lebih cepat tetapi kurang akurat; `medium` lebih akurat tetapi jauh lebih lambat |
| `ZOI_WHISPER_LANGUAGE` | `auto` | Paksa bahasa, misalnya `id` |
| `ZOI_WHISPER_BEAM` | `1` | `5` sedikit lebih akurat dan sedikit lebih lambat |

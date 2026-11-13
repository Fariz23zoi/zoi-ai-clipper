#!/usr/bin/env bash
# ZOI AI CLIPPER untuk laptop Linux/macOS (bisa juga dipakai di Termux).
#   bash mulai.sh                      dashboard hanya untuk perangkat ini
#   ZOI_HOST=0.0.0.0 bash mulai.sh     juga bisa dibuka dari HP di Wi-Fi yang sama
set -e
cd "$(dirname "$0")"

for tool in ffmpeg ffprobe node; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "[!] $tool belum terpasang. Lihat PANDUAN_ZOI.md bagian Memasang."
    exit 1
  fi
done

if [ ! -x .venv/bin/python ]; then
  echo "Menyiapkan Python untuk ZOI. Hanya sekali, mohon tunggu..."
  python3 -m venv .venv
fi

echo "Memeriksa kebutuhan. Pertama kali bisa beberapa menit..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements-termux.txt

# Pengenal suara untuk video tanpa subtitle. Tidak tersedia di Termux.
# Bila gagal dipasang, fitur lain tetap jalan.
if [ -z "${TERMUX_VERSION:-}" ] && [ "${ZOI_SPEECH:-1}" != "0" ]; then
  if ! .venv/bin/python -c "import faster_whisper" >/dev/null 2>&1; then
    echo "Memasang pengenal suara untuk video tanpa subtitle. Sekali saja, bisa beberapa menit..."
    .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements-speech.txt \
      || echo "[!] Pengenal suara gagal dipasang. Video tanpa subtitle belum bisa dianalisis, fitur lain tetap jalan."
  fi
  if ! .venv/bin/python -c "import piper, edge_tts" >/dev/null 2>&1; then
    echo "Memasang suara pembaca untuk pembuka klip. Sekali saja..."
    .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements-voice.txt \
      || echo "[!] Suara pembaca gagal dipasang. Pembuka klip dibuat tanpa suara, fitur lain tetap jalan."
  fi
fi

export ZOI_OPEN_BROWSER="${ZOI_OPEN_BROWSER-1}"
exec .venv/bin/python app.py

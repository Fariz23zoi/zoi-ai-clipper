#!/usr/bin/env bash
# Mengambil versi terbaru ZOI AI CLIPPER. Hentikan dulu aplikasi yang sedang jalan.
set -e
cd "$(dirname "$0")"

echo "Mengambil versi terbaru..."
git pull

if [ -x .venv/bin/python ]; then
  .venv/bin/python -m pip install -q --disable-pip-version-check -U -r requirements-termux.txt
fi

echo "Selesai. Jalankan: bash mulai.sh"

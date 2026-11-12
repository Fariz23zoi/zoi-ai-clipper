@echo off
rem Mengambil versi terbaru ZOI AI CLIPPER. Tutup dulu jendela ZOI yang sedang jalan.
setlocal
cd /d "%~dp0"
title Perbarui ZOI AI CLIPPER

echo Mengambil versi terbaru...
git pull
if errorlevel 1 (echo [!] Pembaruan gagal. Salin pesan di atas dan kirim ke Claude. & pause & exit /b 1)

if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -U -r requirements-termux.txt

echo.
echo Selesai. Jalankan mulai_windows.bat.
pause

@echo off
rem ZOI AI CLIPPER untuk laptop Windows. Klik dua kali file ini.
rem Menyiapkan Python sekali saja, lalu menjalankan dashboard dan membuka browser.
setlocal
cd /d "%~dp0"
title ZOI AI CLIPPER

where ffmpeg >nul 2>nul
if errorlevel 1 (echo [!] FFmpeg belum terpasang. Ikuti PANDUAN_ZOI.md bagian Memasang di laptop Windows. & pause & exit /b 1)

where node >nul 2>nul
if errorlevel 1 (echo [!] Node.js belum terpasang. Ikuti PANDUAN_ZOI.md bagian Memasang di laptop Windows. & pause & exit /b 1)

if not exist ".venv\Scripts\python.exe" echo Menyiapkan Python untuk ZOI. Hanya sekali, mohon tunggu...
rem Python 3.13/3.12 diutamakan: pengenal suara belum tersedia untuk Python yang lebih baru.
if not exist ".venv\Scripts\python.exe" py -3.13 -m venv .venv >nul 2>nul
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv >nul 2>nul
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv >nul 2>nul
if not exist ".venv\Scripts\python.exe" python -m venv .venv >nul 2>nul
if not exist ".venv\Scripts\python.exe" for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do if not exist ".venv\Scripts\python.exe" if exist "%%~D\python.exe" "%%~D\python.exe" -m venv .venv
if not exist ".venv\Scripts\python.exe" (echo [!] Python belum terpasang. Ikuti PANDUAN_ZOI.md bagian Memasang di laptop Windows. & pause & exit /b 1)

echo Memeriksa kebutuhan. Pertama kali bisa beberapa menit...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements-termux.txt
if errorlevel 1 (echo [!] Gagal memasang kebutuhan. Periksa internet, lalu jalankan lagi. & pause & exit /b 1)

rem Pengenal suara untuk video tanpa subtitle. Bila gagal dipasang, fitur lain tetap jalan.
".venv\Scripts\python.exe" -c "import faster_whisper" >nul 2>nul
if errorlevel 1 echo Memasang pengenal suara untuk video tanpa subtitle. Sekali saja, bisa beberapa menit...
if errorlevel 1 ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements-speech.txt
if errorlevel 1 echo [!] Pengenal suara gagal dipasang. Video tanpa subtitle belum bisa dianalisis, fitur lain tetap jalan.

set ZOI_OPEN_BROWSER=1
".venv\Scripts\python.exe" app.py

echo.
echo ZOI AI CLIPPER berhenti.
pause

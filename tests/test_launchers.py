"""Tes file peluncur untuk laptop dan pesan saat aplikasi mulai berjalan.

File .bat tidak bisa dijalankan di luar Windows, jadi di sini hanya
diperiksa bentuknya; mulai.sh diperiksa sintaksnya dengan bash.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

try:
    import app as zoi_app
except ImportError:
    zoi_app = None

ROOT = Path(__file__).resolve().parent.parent
BAT_FILES = ("mulai_windows.bat", "mulai_windows_hp.bat", "perbarui_windows.bat")
SH_FILES = ("mulai.sh", "perbarui.sh")


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class StartupMessageTest(unittest.TestCase):
    def setUp(self):
        self.original = zoi_app._lan_address

    def tearDown(self):
        zoi_app._lan_address = self.original

    def test_default_is_this_device_only(self):
        text = "\n".join(zoi_app._startup_lines("127.0.0.1", 5000))
        self.assertIn("http://127.0.0.1:5000", text)
        self.assertNotIn("Dari HP", text)
        self.assertNotIn("Wi-Fi yang sama", text)

    def test_phone_address_is_shown_when_shared_on_wifi(self):
        zoi_app._lan_address = lambda: "192.168.1.23"
        text = "\n".join(zoi_app._startup_lines("0.0.0.0", 5000))
        self.assertIn("http://127.0.0.1:5000", text)
        self.assertIn("http://192.168.1.23:5000", text)
        # Pengguna diberi tahu bahwa dashboard terbuka untuk satu Wi-Fi.
        self.assertIn("Siapa pun di Wi-Fi yang sama", text)

    def test_missing_wifi_is_explained(self):
        zoi_app._lan_address = lambda: None
        text = "\n".join(zoi_app._startup_lines("0.0.0.0", 5000))
        self.assertIn("tidak terbaca", text)

    def test_local_url(self):
        self.assertEqual(zoi_app._local_url("0.0.0.0", 5000), "http://127.0.0.1:5000")
        self.assertEqual(zoi_app._local_url("127.0.0.1", 81), "http://127.0.0.1:81")
        self.assertEqual(
            zoi_app._local_url("192.168.1.9", 5000), "http://192.168.1.9:5000"
        )

    def test_lan_address_is_an_ip_or_none(self):
        address = self.original()
        if address is not None:
            parts = address.split(".")
            self.assertEqual(len(parts), 4)
            self.assertFalse(address.startswith("127."))

    def test_open_browser_never_raises(self):
        import webbrowser

        original = webbrowser.open

        def boom(url):
            raise RuntimeError("tidak ada browser")

        webbrowser.open = boom
        try:
            zoi_app._open_browser("http://127.0.0.1:5000")
        finally:
            webbrowser.open = original


class LauncherFilesTest(unittest.TestCase):
    def read(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_launchers_exist(self):
        for name in BAT_FILES + SH_FILES:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_requirements_file_named_in_launchers_exists(self):
        self.assertTrue((ROOT / "requirements-termux.txt").is_file())
        for name in ("mulai_windows.bat", "perbarui_windows.bat") + SH_FILES:
            self.assertIn("requirements-termux.txt", self.read(name), name)

    def test_bat_files_use_only_single_line_blocks(self):
        # Blok bertanda kurung yang melewati beberapa baris mudah rusak
        # oleh teks yang mengandung tanda kurung, jadi dihindari.
        for name in BAT_FILES:
            for number, line in enumerate(self.read(name).splitlines(), 1):
                self.assertEqual(
                    line.count("("), line.count(")"), f"{name}:{number}: {line}"
                )
                self.assertNotIn("goto", line.lower(), f"{name}:{number}")

    def test_bat_messages_avoid_characters_special_to_cmd(self):
        for name in BAT_FILES:
            for number, line in enumerate(self.read(name).splitlines(), 1):
                if "(echo " not in line:
                    continue
                message = line.split("(echo ", 1)[1].split(" & pause", 1)[0]
                for character in "()|<>^%":
                    self.assertNotIn(
                        character, message, f"{name}:{number}: {message}"
                    )

    def test_main_windows_launcher_steps(self):
        text = self.read("mulai_windows.bat")
        for expected in (
            'cd /d "%~dp0"', "where ffmpeg", "where node",
            "py -3 -m venv .venv", "python -m venv .venv",
            "pip install", "set ZOI_OPEN_BROWSER=1",
            '".venv\\Scripts\\python.exe" app.py',
        ):
            self.assertIn(expected, text)
        # Setiap pemeriksaan yang gagal berhenti dengan pesan, tidak menutup
        # jendela begitu saja.
        self.assertGreaterEqual(text.count("& pause & exit /b 1)"), 4)

    def test_speech_recognition_is_installed_but_not_required(self):
        self.assertTrue((ROOT / "requirements-speech.txt").is_file())
        self.assertIn(
            "faster-whisper",
            (ROOT / "requirements-speech.txt").read_text(encoding="utf-8"),
        )

        text = self.read("mulai_windows.bat")
        start = text.index("import faster_whisper")
        block = text[start:text.index("set ZOI_OPEN_BROWSER")]
        self.assertIn("requirements-speech.txt", block)
        # Kegagalan memasang tidak menghentikan peluncur.
        self.assertNotIn("exit /b", block)
        self.assertLess(start, text.index('".venv\\Scripts\\python.exe" app.py'))

        text = self.read("mulai.sh")
        self.assertIn("requirements-speech.txt", text)
        self.assertIn("TERMUX_VERSION", text)
        self.assertIn('|| echo "[!] Pengenal suara gagal dipasang', text)

    def test_voice_is_installed_but_not_required(self):
        self.assertIn("piper-tts", (ROOT / "requirements-voice.txt").read_text(encoding="utf-8"))
        text = self.read("mulai_windows.bat")
        start = text.index("import piper")
        block = text[start:text.index("set ZOI_OPEN_BROWSER")]
        self.assertIn("requirements-voice.txt", block)
        self.assertNotIn("exit /b", block)
        self.assertIn("requirements-voice.txt", self.read("mulai.sh"))

    def test_phone_launcher_shares_on_wifi_and_reuses_main_launcher(self):
        text = self.read("mulai_windows_hp.bat")
        self.assertIn("set ZOI_HOST=0.0.0.0", text)
        self.assertIn('call "%~dp0mulai_windows.bat"', text)

    def test_update_launcher_pulls_then_updates_packages(self):
        text = self.read("perbarui_windows.bat")
        self.assertLess(text.index("git pull"), text.index("pip install"))

    def test_gitattributes_keeps_windows_line_endings_for_bat(self):
        text = self.read(".gitattributes")
        self.assertIn("*.bat text eol=crlf", text)
        self.assertIn("*.sh text eol=lf", text)

    @unittest.skipUnless(shutil.which("bash"), "bash tidak terpasang")
    def test_shell_launchers_have_valid_syntax(self):
        for name in SH_FILES:
            result = subprocess.run(
                ["bash", "-n", str(ROOT / name)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_shell_launcher_checks_tools_and_starts_the_app(self):
        text = self.read("mulai.sh")
        for expected in ("ffmpeg", "node", "python3 -m venv .venv", "app.py"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()

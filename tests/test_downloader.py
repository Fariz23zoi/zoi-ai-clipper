"""Tes pemilihan format unduhan. Memakai mesin pemilih milik yt-dlp sendiri
dengan daftar format tiruan, jadi tidak membutuhkan internet."""
import copy
import os
import shutil
import tempfile
import unittest

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from shorts_generator.local import downloader


def _video(format_id, width, height, codec, fps=30, ext="mp4"):
    return {
        "format_id": format_id, "url": f"http://contoh/{format_id}", "ext": ext,
        "width": width, "height": height, "vcodec": codec, "acodec": "none",
        "fps": fps, "tbr": width * height * fps / 40000, "protocol": "https",
    }


def _audio(format_id, ext, codec, bitrate):
    return {
        "format_id": format_id, "url": f"http://contoh/{format_id}", "ext": ext,
        "vcodec": "none", "acodec": codec, "abr": bitrate, "tbr": bitrate,
        "protocol": "https",
    }


AUDIO = [
    _audio("249", "webm", "opus", 50),
    _audio("140", "m4a", "mp4a.40.2", 129),
    _audio("251", "webm", "opus", 160),
]
MUXED = [{
    "format_id": "18", "url": "http://contoh/18", "ext": "mp4", "width": 640,
    "height": 360, "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "fps": 30,
    "tbr": 500, "protocol": "https",
}]

# Susunan format yang lazim pada video YouTube mendatar 1080p60.
LANDSCAPE = AUDIO + MUXED + [
    _video("134", 640, 360, "avc1.4d401e"),
    _video("135", 854, 480, "avc1.4d401f"),
    _video("244", 854, 480, "vp09.00.30.08", ext="webm"),
    _video("397", 854, 480, "av01.0.04M.08"),
    _video("136", 1280, 720, "avc1.4d401f"),
    _video("398", 1280, 720, "av01.0.05M.08"),
    _video("298", 1280, 720, "avc1.4d4020", fps=60),
    _video("137", 1920, 1080, "avc1.640028"),
    _video("299", 1920, 1080, "avc1.64002a", fps=60),
]
PORTRAIT = AUDIO + [
    _video("133", 240, 426, "avc1.4d4015"),
    _video("134", 360, 640, "avc1.4d401e"),
    _video("135", 480, 854, "avc1.4d401f"),
    _video("136", 720, 1280, "avc1.4d401f"),
]


@unittest.skipIf(yt_dlp is None, "yt-dlp tidak terpasang")
class FormatSelectionTest(unittest.TestCase):
    def pick(self, formats, height):
        info = {
            "id": "x", "title": "t", "extractor": "test",
            "extractor_key": "Test", "webpage_url": "http://contoh",
            "formats": copy.deepcopy(formats),
        }
        options = {
            "quiet": True, "no_warnings": True, "simulate": True,
            **downloader._format_options(height),
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.process_ie_result(info, download=False).get("format_id")

    def test_light_profile_picks_480p_30fps_h264(self):
        self.assertEqual(self.pick(LANDSCAPE, "480"), "135+140")

    def test_sharp_profile_picks_720p_30fps_h264_not_60fps_or_av1(self):
        self.assertEqual(self.pick(LANDSCAPE, "720"), "136+140")

    def test_portrait_video_is_measured_by_its_short_side(self):
        self.assertEqual(self.pick(PORTRAIT, "480"), "135+140")
        self.assertEqual(self.pick(PORTRAIT, "720"), "136+140")

    def test_lower_resolution_is_used_when_target_is_missing(self):
        formats = AUDIO + [_video("134", 640, 360, "avc1.4d401e")]
        self.assertEqual(self.pick(formats, "480"), "134+140")

    def test_other_codec_is_used_when_h264_is_missing(self):
        formats = AUDIO + [_video("397", 854, 480, "av01.0.04M.08")]
        self.assertEqual(self.pick(formats, "480"), "397+140")

    def test_60fps_is_used_when_nothing_slower_exists(self):
        formats = AUDIO + [_video("298", 1280, 720, "avc1.4d4020", fps=60)]
        self.assertEqual(self.pick(formats, "480"), "298+140")

    def test_single_file_format_still_works(self):
        self.assertEqual(self.pick(MUXED, "480"), "18")

    def pick_audio(self, formats):
        info = {
            "id": "x", "title": "t", "extractor": "test",
            "extractor_key": "Test", "webpage_url": "http://contoh",
            "formats": copy.deepcopy(formats),
        }
        options = {
            "quiet": True, "no_warnings": True, "simulate": True,
            **downloader.AUDIO_FORMAT_OPTIONS,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.process_ie_result(info, download=False).get("format_id")

    def test_audio_for_speech_is_about_64_kbps_and_audio_only(self):
        self.assertEqual(self.pick_audio(LANDSCAPE), "249")
        ultralow = [_audio("600", "webm", "opus", 35)]
        self.assertEqual(self.pick_audio(LANDSCAPE + ultralow), "249")
        self.assertEqual(
            self.pick_audio([_audio("140", "m4a", "mp4a.40.2", 129)] + ultralow),
            "600",
        )

    def test_audio_falls_back_to_a_single_file_format(self):
        self.assertEqual(self.pick_audio(MUXED), "18")

    def test_bad_height_falls_back_to_720(self):
        self.assertEqual(
            downloader._format_options("abc")["format_sort"][0], "res:720"
        )


@unittest.skipIf(yt_dlp is None, "yt-dlp tidak terpasang")
class DownloadCallTest(unittest.TestCase):
    """download_youtube_local() dengan yt-dlp tiruan."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.seen = {}
        test = self

        class FakeYDL:
            def __init__(self, params):
                test.seen["params"] = params

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                test.seen["url"] = url
                path = os.path.join(test.folder, "source_ABCDEFGHIJK.mp4")
                with open(path, "wb") as file:
                    file.write(b"video")
                return {"id": "ABCDEFGHIJK", "ext": "mp4"}

            def prepare_filename(self, info):
                return os.path.join(test.folder, "source_ABCDEFGHIJK.mp4")

        self.original = yt_dlp.YoutubeDL
        yt_dlp.YoutubeDL = FakeYDL

    def tearDown(self):
        yt_dlp.YoutubeDL = self.original
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_options_passed_to_ytdlp(self):
        hook = lambda status: None
        path = downloader.download_youtube_local(
            "https://www.youtube.com/watch?v=ABCDEFGHIJK", fmt="480",
            out_dir=self.folder, progress_hook=hook,
        )
        params = self.seen["params"]
        self.assertEqual(path, os.path.join(self.folder, "source_ABCDEFGHIJK.mp4"))
        self.assertEqual(params["format_sort"], ["res:480", "fps:30", "vcodec:h264"])
        self.assertEqual(params["progress_hooks"], [hook])
        self.assertEqual(params["merge_output_format"], "mp4")
        self.assertTrue(params["outtmpl"].startswith(self.folder))

    def test_existing_download_is_reused_without_network(self):
        cached = os.path.join(self.folder, "source_ABCDEFGHIJK.mp4")
        with open(cached, "wb") as file:
            file.write(b"video")

        path = downloader.download_youtube_local(
            "https://youtu.be/ABCDEFGHIJK", fmt="480", out_dir=self.folder
        )
        self.assertEqual(path, cached)
        self.assertNotIn("params", self.seen)


if __name__ == "__main__":
    unittest.main()

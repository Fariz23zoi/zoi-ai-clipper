"""Tes rendering FFmpeg: potong, 9:16, audio, progres, pembersihan."""
import os
import shutil
import tempfile
import unittest

from shorts_generator.local import clipper

from tests.helpers import ffmpeg_available, make_test_video, probe


@unittest.skipUnless(ffmpeg_available(), "FFmpeg/ffprobe tidak terpasang")
class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = make_test_video(os.path.join(cls.folder, "source_uji.mp4"))
        cls.output = os.path.join(cls.folder, "hasil.mp4")
        cls.updates = []
        clipper.crop_clip_local(
            cls.source, 5, 37, "9:16", cls.output,
            progress_callback=lambda percent, stage: cls.updates.append(
                (percent, stage)
            ),
        )
        cls.streams = probe(cls.output)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_output_is_vertical_720x1280_h264(self):
        video = self.streams["video"]
        self.assertEqual((video["width"], video["height"]), (720, 1280))
        self.assertEqual(video["codec"], "h264")

    def test_audio_is_kept(self):
        self.assertEqual(self.streams["audio"]["codec"], "aac")

    def test_duration_matches_requested_range(self):
        self.assertAlmostEqual(self.streams["video"]["duration"], 32.0, delta=0.5)
        self.assertAlmostEqual(self.streams["audio"]["duration"], 32.0, delta=0.5)

    def test_progress_is_real_monotonic_and_reaches_100(self):
        percents = [percent for percent, _ in self.updates]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)
        # Banyak nilai antara berarti angka berasal dari FFmpeg.
        self.assertGreater(len(set(percents)), 8)
        self.assertTrue(all(0 <= percent <= 100 for percent in percents))

    def test_source_is_untouched_and_temp_files_removed(self):
        self.assertTrue(os.path.isfile(self.source))
        self.assertEqual(
            sorted(os.listdir(self.folder)), ["hasil.mp4", "source_uji.mp4"]
        )

    def test_failed_render_raises_and_leaves_nothing(self):
        target = os.path.join(self.folder, "gagal.mp4")
        with self.assertRaises(RuntimeError):
            clipper.crop_clip_local(
                os.path.join(self.folder, "tidak_ada.mp4"), 0, 40, "9:16", target
            )
        self.assertFalse(
            [name for name in os.listdir(self.folder) if name.startswith("gagal")]
        )

    def test_vertical_source_also_renders(self):
        source = make_test_video(
            os.path.join(self.folder, "tegak.mp4"), seconds=8, size="540x960"
        )
        target = os.path.join(self.folder, "tegak_hasil.mp4")
        try:
            clipper.crop_clip_local(source, 1, 7, "9:16", target)
            video = probe(target)["video"]
            self.assertEqual((video["width"], video["height"]), (720, 1280))
        finally:
            for path in (source, target):
                if os.path.exists(path):
                    os.remove(path)


def _frame_rate(path):
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=avg_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    number, _, divisor = out.partition("/")
    return float(number) / float(divisor or 1)


def _make_source(path, rate):
    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=640x360:rate={rate}",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(path)],
        check=True,
    )
    return str(path)


@unittest.skipUnless(ffmpeg_available(), "FFmpeg/ffprobe tidak terpasang")
class FrameRateLimitTest(unittest.TestCase):
    """Sumber 50/60 fps dirender separuhnya; sumber biasa tidak diubah."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def render(self, rate):
        source = _make_source(os.path.join(self.folder, f"s{rate}.mp4"), rate)
        target = os.path.join(self.folder, f"o{rate}.mp4")
        clipper.crop_clip_local(source, 0, 5, "9:16", target)
        return _frame_rate(target), clipper._fps_limit_filter(source)

    def test_60fps_source_is_rendered_at_30(self):
        rate, limit = self.render(60)
        self.assertAlmostEqual(rate, 30, delta=0.5)
        self.assertEqual(limit, "fps=60/2,")

    def test_50fps_source_is_rendered_at_25(self):
        rate, limit = self.render(50)
        self.assertAlmostEqual(rate, 25, delta=0.5)
        self.assertEqual(limit, "fps=50/2,")

    def test_30_and_25fps_sources_are_left_alone(self):
        for source_rate in (30, 25):
            rate, limit = self.render(source_rate)
            self.assertAlmostEqual(rate, source_rate, delta=0.5)
            self.assertEqual(limit, "")

    def test_limit_can_be_turned_off(self):
        source = _make_source(os.path.join(self.folder, "bebas.mp4"), 60)
        saved = clipper.RENDER_MAX_FPS
        clipper.RENDER_MAX_FPS = 0
        try:
            self.assertEqual(clipper._fps_limit_filter(source), "")
        finally:
            clipper.RENDER_MAX_FPS = saved

    def test_unreadable_file_is_left_alone(self):
        self.assertEqual(
            clipper._fps_limit_filter(os.path.join(self.folder, "tidak_ada.mp4")), ""
        )


@unittest.skipUnless(ffmpeg_available(), "FFmpeg/ffprobe tidak terpasang")
class RenderSizeTest(unittest.TestCase):
    """Setiap pilihan kualitas menghasilkan kanvas 9:16 berukuran tepat."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = make_test_video(
            os.path.join(cls.folder, "sumber.mp4"), seconds=6, size="640x360"
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_sizes_offered(self):
        self.assertEqual(
            clipper.RENDER_SIZES,
            {"240": (240, 426), "360": (360, 640), "480": (480, 854),
             "720": (720, 1280), "1080": (1080, 1920)},
        )
        for width, height in clipper.RENDER_SIZES.values():
            # H.264 yuv420p butuh ukuran genap; rasio harus 9:16.
            self.assertEqual((width % 2, height % 2), (0, 0))
            self.assertAlmostEqual(width / height, 9 / 16, delta=0.002)

    def test_every_size_renders_with_audio(self):
        for quality, size in clipper.RENDER_SIZES.items():
            target = os.path.join(self.folder, f"hasil_{quality}.mp4")
            clipper.crop_clip_local(
                self.source, 1, 5, "9:16", target, size=size
            )
            streams = probe(target)
            self.assertEqual(
                (streams["video"]["width"], streams["video"]["height"]), size
            )
            self.assertEqual(streams["audio"]["codec"], "aac")
            self.assertAlmostEqual(streams["video"]["duration"], 4.0, delta=0.5)

    def test_without_size_the_default_is_720x1280(self):
        target = os.path.join(self.folder, "bawaan.mp4")
        clipper.crop_clip_local(self.source, 1, 5, "9:16", target)
        video = probe(target)["video"]
        self.assertEqual((video["width"], video["height"]), (720, 1280))


@unittest.skipUnless(ffmpeg_available(), "FFmpeg/ffprobe tidak terpasang")
class SquareFrameTest(unittest.TestCase):
    """Bingkai 1:1: bagian tengah frame dipotong persegi selebar kanvas."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = make_test_video(
            os.path.join(cls.folder, "lebar.mp4"), seconds=6, size="1280x720"
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_filters(self):
        self.assertIn("crop='min(iw,ih)':'min(iw,ih)'", clipper._foreground_filter(720, 1280, "square"))
        self.assertIn("scale=720:720", clipper._foreground_filter(720, 1280, "square"))
        self.assertIn("force_original_aspect_ratio=decrease", clipper._foreground_filter(720, 1280, "wide"))

    def test_square_frame_renders_9_16_with_audio(self):
        target = os.path.join(self.folder, "persegi.mp4")
        clipper.crop_clip_local(self.source, 1, 5, "9:16", target, size=(720, 1280), frame="square")
        streams = probe(target)
        self.assertEqual((streams["video"]["width"], streams["video"]["height"]), (720, 1280))
        self.assertIn("audio", streams)

    def test_square_frame_shows_a_taller_picture_than_wide(self):
        import subprocess

        def picture_rows(path):
            # Baris yang berbeda tajam dari latar blur = gambar utama.
            out = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", "2", "-i", path, "-frames:v", "1",
                 "-vf", "scale=36:64,format=gray", "-f", "rawvideo", "-"],
                capture_output=True, check=True,
            ).stdout
            rows = [out[i * 36:(i + 1) * 36] for i in range(64)]
            return sum(1 for row in rows if max(row) - min(row) > 120)

        wide = os.path.join(self.folder, "w.mp4")
        square = os.path.join(self.folder, "s.mp4")
        clipper.crop_clip_local(self.source, 1, 4, "9:16", wide, size=(360, 640), frame="wide")
        clipper.crop_clip_local(self.source, 1, 4, "9:16", square, size=(360, 640), frame="square")
        self.assertGreater(picture_rows(square), picture_rows(wide))

    def test_unknown_frame_is_refused(self):
        with self.assertRaises(ValueError):
            clipper.crop_clip_local(self.source, 1, 4, "9:16",
                                    os.path.join(self.folder, "x.mp4"), frame="bulat")


class HookLayoutTest(unittest.TestCase):
    def test_markers_emoji_and_spaces_are_cleaned(self):
        self.assertEqual(
            clipper.clean_hook_text("  Lucu [tertawa]  banget 😂\n ya  "),
            "Lucu banget ya",
        )

    def test_short_text_uses_the_largest_font(self):
        size, lines = clipper.layout_hook("Kenapa harganya mahal?", 720)
        self.assertEqual(lines, ["Kenapa harganya mahal?"])
        self.assertEqual(size, int(720 * 0.062))

    def test_long_text_wraps_shrinks_and_is_cut_to_three_lines(self):
        text = "kata " * 60
        size, lines = clipper.layout_hook(text, 720)
        self.assertLessEqual(len(lines), clipper.HOOK_MAX_LINES)
        self.assertLess(size, int(720 * 0.062))
        self.assertTrue(lines[-1].endswith("..."))

    def test_empty_text_gives_no_lines(self):
        self.assertEqual(clipper.layout_hook("  [musik]  ", 720), (0, []))

    def test_crop_position_expression(self):
        left = clipper._foreground_filter(720, 1280, "square", 0.0)
        right = clipper._foreground_filter(720, 1280, "square", 1.0)
        clamped = clipper._foreground_filter(720, 1280, "square", 7)
        self.assertIn("*0.000", left)
        self.assertIn("*1.000", right)
        self.assertIn("*1.000", clamped)


@unittest.skipUnless(ffmpeg_available() and clipper.find_hook_font(), "FFmpeg atau font tidak tersedia")
class HookRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = make_test_video(os.path.join(cls.folder, "src.mp4"), seconds=6, size="1280x720")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def top_area(self, path):
        import subprocess
        # Bagian atas kanvas diperkecil jadi 36 x 20 piksel abu-abu.
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "2", "-i", path, "-frames:v", "1",
             "-vf", "crop=iw:ih*0.3:0:0,scale=36:20,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout

    def test_hook_block_is_drawn_in_the_top_area(self):
        plain = os.path.join(self.folder, "polos.mp4")
        hooked = os.path.join(self.folder, "hook.mp4")
        clipper.crop_clip_local(self.source, 1, 4, "9:16", plain, size=(360, 640))
        clipper.crop_clip_local(
            self.source, 1, 4, "9:16", hooked, size=(360, 640),
            hook_text="Harga 100%: \"mahal\" banget, 'kan? C:\\folder {x} [tertawa] 😂",
        )
        self.assertEqual(probe(hooked)["video"]["width"], 360)
        self.assertIn("audio", probe(hooked))

        before, after = self.top_area(plain), self.top_area(hooked)
        changed = sum(1 for a, b in zip(before, after) if abs(a - b) > 40)
        # Kotak dan teks hook mengubah banyak piksel di bagian atas...
        self.assertGreater(changed, 60)

        # ...tetapi tidak menyentuh bagian bawah video.
        def bottom(path):
            import subprocess
            return subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", "2", "-i", path, "-frames:v", "1",
                 "-vf", "crop=iw:ih*0.25:0:ih*0.75,scale=36:16,format=gray", "-f", "rawvideo", "-"],
                capture_output=True, check=True,
            ).stdout
        diff = sum(1 for a, b in zip(bottom(plain), bottom(hooked)) if abs(a - b) > 40)
        self.assertLess(diff, 5)

    def test_hook_with_square_frame_and_crop_position(self):
        target = os.path.join(self.folder, "persegi_hook.mp4")
        clipper.crop_clip_local(
            self.source, 1, 4, "9:16", target, size=(480, 854), frame="square",
            crop_x=0.2, hook_text="Ternyata ini rahasianya",
        )
        video = probe(target)["video"]
        self.assertEqual((video["width"], video["height"]), (480, 854))

    def test_temporary_files_are_removed(self):
        before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("zoi_hook_")}
        clipper.crop_clip_local(
            self.source, 1, 3, "9:16", os.path.join(self.folder, "bersih.mp4"),
            size=(240, 426), hook_text="Tes",
        )
        after = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("zoi_hook_")}
        self.assertEqual(after - before, set())

    def test_missing_font_is_explained(self):
        saved = (clipper._HOOK_FONTS, os.environ.get("ZOI_HOOK_FONT"))
        clipper._HOOK_FONTS = ()
        os.environ.pop("ZOI_HOOK_FONT", None)
        try:
            with self.assertRaises(RuntimeError) as caught:
                clipper.crop_clip_local(
                    self.source, 1, 3, "9:16", os.path.join(self.folder, "tanpa_font.mp4"),
                    size=(240, 426), hook_text="Tes",
                )
            self.assertIn("Font untuk blok hook", str(caught.exception))
        finally:
            clipper._HOOK_FONTS = saved[0]
            if saved[1] is not None:
                os.environ["ZOI_HOOK_FONT"] = saved[1]


class PriorityTest(unittest.TestCase):
    def test_ffmpeg_runs_at_low_priority_when_nice_exists(self):
        command = clipper._with_low_priority(["ffmpeg", "-y"])
        if shutil.which("nice"):
            self.assertEqual(command[:3], ["nice", "-n", str(clipper.RENDER_NICE)])
        self.assertEqual(command[-2:], ["ffmpeg", "-y"])

    def test_priority_change_can_be_turned_off(self):
        saved = clipper.RENDER_NICE
        clipper.RENDER_NICE = 0
        try:
            self.assertEqual(
                clipper._with_low_priority(["ffmpeg", "-y"]), ["ffmpeg", "-y"]
            )
        finally:
            clipper.RENDER_NICE = saved


if __name__ == "__main__":
    unittest.main()

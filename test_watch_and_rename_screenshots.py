#!/usr/bin/python3
"""Tests for the screenshot-move logic in watch-and-rename-screenshots.py.

Run from the repo root:
    python3 -m unittest test_watch_and_rename_screenshots -v
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parent / "watch-and-rename-screenshots.py"

spec = importlib.util.spec_from_file_location("renamer", SCRIPT)
renamer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renamer)

TS = "2026-06-12--08-40-50"


class MoveScreenshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self.raw_dir = tmp_path / "raw"
        self.dest_dir = tmp_path / "desktop"
        self.raw_dir.mkdir()
        self.dest_dir.mkdir()
        renamer.DEST_DIR = self.dest_dir
        renamer._burst_timestamp = None
        renamer._burst_count = 0

    def tearDown(self):
        self.tmp.cleanup()

    def make_raw(self, name: str, content: bytes) -> Path:
        path = self.raw_dir / name
        path.write_bytes(content)
        return path

    def dest_names(self):
        return sorted(p.name for p in self.dest_dir.iterdir())

    def test_single_screenshot_gets_plain_name(self):
        raw = self.make_raw("Screenshot 1.png", b"one")
        dest = renamer.move_screenshot(raw, TS)
        self.assertEqual(dest.name, f"screenshot-{TS}.png")
        self.assertEqual(self.dest_names(), [f"screenshot-{TS}.png"])
        self.assertEqual(dest.read_bytes(), b"one")
        self.assertFalse(raw.exists())

    def test_two_screenshots_same_timestamp_get_suffixes(self):
        raw1 = self.make_raw("Screenshot 1.png", b"one")
        raw2 = self.make_raw("Screenshot 1 (2).png", b"two")
        renamer.move_screenshot(raw1, TS)
        renamer.move_screenshot(raw2, TS)
        self.assertEqual(
            self.dest_names(),
            [f"screenshot-{TS}--1.png", f"screenshot-{TS}--2.png"],
        )
        self.assertEqual((self.dest_dir / f"screenshot-{TS}--1.png").read_bytes(), b"one")
        self.assertEqual((self.dest_dir / f"screenshot-{TS}--2.png").read_bytes(), b"two")

    def test_three_screenshots_same_timestamp(self):
        contents = [b"one", b"two", b"three"]
        for i, content in enumerate(contents):
            raw = self.make_raw(f"Screenshot {i}.png", content)
            renamer.move_screenshot(raw, TS)
        self.assertEqual(
            self.dest_names(),
            [f"screenshot-{TS}--{n}.png" for n in (1, 2, 3)],
        )
        for n, content in zip((1, 2, 3), contents):
            self.assertEqual(
                (self.dest_dir / f"screenshot-{TS}--{n}.png").read_bytes(), content
            )

    def test_different_timestamps_each_get_plain_names(self):
        raw1 = self.make_raw("Screenshot 1.png", b"one")
        raw2 = self.make_raw("Screenshot 2.png", b"two")
        renamer.move_screenshot(raw1, TS)
        renamer.move_screenshot(raw2, "2026-06-12--08-40-51")
        self.assertEqual(
            self.dest_names(),
            [f"screenshot-{TS}.png", "screenshot-2026-06-12--08-40-51.png"],
        )

    def test_never_overwrites_even_if_script_restarted_mid_burst(self):
        # First screenshot of a burst was already moved, then the script
        # restarted and lost its in-memory burst state.
        existing = self.dest_dir / f"screenshot-{TS}.png"
        existing.write_bytes(b"one")
        renamer._burst_timestamp = None
        renamer._burst_count = 0

        raw = self.make_raw("Screenshot 1 (2).png", b"two")
        renamer.move_screenshot(raw, TS)

        self.assertEqual(
            self.dest_names(),
            [f"screenshot-{TS}--1.png", f"screenshot-{TS}--2.png"],
        )
        self.assertEqual((self.dest_dir / f"screenshot-{TS}--1.png").read_bytes(), b"one")
        self.assertEqual((self.dest_dir / f"screenshot-{TS}--2.png").read_bytes(), b"two")


class ParseFswatchLineTest(unittest.TestCase):
    # fswatch is invoked with --event-flag-separator=| so the flags form one
    # space-free token after the path.

    def test_simple_path(self):
        self.assertEqual(
            renamer.parse_fswatch_line("/raw/Screenshot 1.png IsFile|Renamed"),
            ("/raw/Screenshot 1.png", "IsFile|Renamed"),
        )

    def test_path_containing_png_space_mid_name(self):
        self.assertEqual(
            renamer.parse_fswatch_line("/raw/weird.png name.png IsFile"),
            ("/raw/weird.png name.png", "IsFile"),
        )

    def test_non_png_returns_none(self):
        self.assertIsNone(renamer.parse_fswatch_line("/raw/.DS_Store IsFile|Updated"))

    def test_temp_screenshot_file_returns_none(self):
        self.assertIsNone(
            renamer.parse_fswatch_line("/raw/..Screenshot 1.png-oQAC IsFile|Created")
        )

    def test_line_without_space_returns_none(self):
        self.assertIsNone(renamer.parse_fswatch_line("garbage"))


class CheckFswatchInstalledTest(unittest.TestCase):
    def test_missing_fswatch_notifies_sleeps_and_exits(self):
        with mock.patch.object(renamer, "FSWATCH_PATH", "/nonexistent/fswatch"), \
             mock.patch.object(renamer, "notify") as notify, \
             mock.patch.object(renamer.time, "sleep") as sleep:
            with self.assertRaises(SystemExit):
                renamer.check_fswatch_installed()
            notify.assert_called_once()
            sleep.assert_called_once_with(3600)

    def test_present_fswatch_is_quiet(self):
        with mock.patch.object(renamer, "FSWATCH_PATH", "/bin/ls"), \
             mock.patch.object(renamer, "notify") as notify:
            renamer.check_fswatch_installed()
            notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()

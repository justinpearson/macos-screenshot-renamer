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


class ScreencaptureSettingsCheckTest(unittest.TestCase):
    def patch_defaults(self, location: str, target: str = "file"):
        values = {"location": location, "target": target}
        return mock.patch.object(
            renamer, "read_screencapture_default", side_effect=values.__getitem__
        )

    def test_ok_when_location_is_raw_dir(self):
        with self.patch_defaults(str(renamer.RAW_DIR)):
            self.assertTrue(renamer.screencapture_settings_ok())

    def test_not_ok_when_location_unset(self):
        # QuickTime's "Save To > Desktop" and "Save To > QuickTime Player"
        # both DELETE the key rather than writing a path.
        with self.patch_defaults(""):
            self.assertFalse(renamer.screencapture_settings_ok())

    def test_not_ok_when_location_is_other_path(self):
        with self.patch_defaults("~/Documents/"):
            self.assertFalse(renamer.screencapture_settings_ok())

    def test_ok_when_target_unset(self):
        # Absent target means the default behavior: save to a file.
        with self.patch_defaults(str(renamer.RAW_DIR), target=""):
            self.assertTrue(renamer.screencapture_settings_ok())

    def test_not_ok_when_target_is_preview(self):
        # QuickTime's "Save To > QuickTime Player" sets target=preview:
        # Cmd-Shift-3 then opens Preview and writes no file anywhere.
        with self.patch_defaults(str(renamer.RAW_DIR), target="preview"):
            self.assertFalse(renamer.screencapture_settings_ok())

    def test_not_ok_when_target_is_clipboard(self):
        with self.patch_defaults(str(renamer.RAW_DIR), target="clipboard"):
            self.assertFalse(renamer.screencapture_settings_ok())

    def test_check_warns_and_notifies_when_location_broken(self):
        with self.patch_defaults(""), \
             mock.patch.object(renamer, "notify") as notify:
            self.assertFalse(renamer.check_screencapture_settings())
            notify.assert_called_once()

    def test_check_names_target_when_target_broken(self):
        with self.patch_defaults(str(renamer.RAW_DIR), target="preview"), \
             mock.patch.object(renamer, "notify") as notify:
            self.assertFalse(renamer.check_screencapture_settings())
            notify.assert_called_once()
            self.assertIn("target", notify.call_args.args[0])

    def test_check_is_quiet_when_ok(self):
        with self.patch_defaults(str(renamer.RAW_DIR)), \
             mock.patch.object(renamer, "notify") as notify:
            self.assertTrue(renamer.check_screencapture_settings())
            notify.assert_not_called()


class _StopMonitor(Exception):
    """Raised from a mocked time.sleep to end the monitor's infinite loop."""


class MonitorScreencaptureSettingsTest(unittest.TestCase):
    def run_monitor(self, ok_values):
        """Run monitor_screencapture_settings for len(ok_values) checks.

        Returns the mock for warn_screencapture_settings_broken.
        """
        sleeps = [None] * len(ok_values) + [_StopMonitor()]
        with mock.patch.object(renamer.time, "sleep", side_effect=sleeps), \
             mock.patch.object(
                 renamer, "screencapture_settings_ok", side_effect=list(ok_values)
             ), \
             mock.patch.object(
                 renamer, "warn_screencapture_settings_broken"
             ) as warn:
            with self.assertRaises(_StopMonitor):
                renamer.monitor_screencapture_settings()
        return warn

    def test_never_warns_while_ok(self):
        warn = self.run_monitor([True, True, True])
        warn.assert_not_called()

    def test_warns_once_when_broken_and_stays_broken(self):
        # One notification per breakage, not one every interval.
        warn = self.run_monitor([True, False, False, False])
        warn.assert_called_once()

    def test_warns_again_after_recovery_and_rebreak(self):
        warn = self.run_monitor([False, True, False])
        self.assertEqual(warn.call_count, 2)


if __name__ == "__main__":
    unittest.main()

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a macOS utility that automatically renames screenshots from macOS's default format (which contains a problematic unicode narrow no-break space U+202F before AM/PM) to a simpler format: `screenshot-yyyy-mm-dd--hh-mm-ss.png`.

The system has three parts:
1. macOS is configured to save screenshots to `raw-screenshots/` instead of Desktop, via `defaults write com.apple.screencapture location ...`
2. A Python script (`watch-and-rename-screenshots.py`) uses `fswatch` to monitor that directory
3. A launchd job (`com.justin.macos-screenshot-renamer.plist`) keeps the Python script running

## Known Failure Mode

macOS updates have been observed to silently reset `com.apple.screencapture location` back to its default. When this happens, screenshots go straight to `~/Desktop` (with the U+202F filenames), `fswatch` sees nothing in `raw-screenshots/`, and the user gets no obvious indication the renamer is broken.

To detect this, the script runs a startup self-check (`check_screencapture_location()`) that compares the current `defaults` value against `RAW_DIR`. On mismatch it logs a WARNING and posts a macOS notification titled "Screenshot renamer broken". The launchd job restarts the script on system boot, so the post-update reboot is a natural moment for this check to fire.

To fix the `defaults` reset:
```zsh
defaults write com.apple.screencapture location /Users/justin/Utilities/macos-screenshot-renamer/raw-screenshots
killall SystemUIServer
```

A second failure mode: if the `fswatch` binary disappears (e.g. removed by a Homebrew upgrade), the script would crash-loop under launchd with no signal. A startup check (`check_fswatch_installed()`) logs, notifies, sleeps an hour, and exits—so the user gets at most one notification per hour and the job recovers on its own once fswatch is reinstalled.

## Architecture Notes

The Python script handles several macOS quirks:
- macOS writes screenshots in three stages (temp file → hidden file → final file), so the script filters by filename pattern to only process the final file
- The script uses `lsof` to wait until no process has the file open before moving it
- `fswatch` fires multiple events per file; after moving, subsequent events are ignored via the file-exists check
- launchd jobs cannot READ from ~/Desktop without Full Disk Access, but CAN write to it—hence the two-directory approach. More precisely (verified experimentally 2026-06): TCC blocks listing ~/Desktop, reading its files, and hard-linking into it, but allows stat, rename-into, rename-within, and unlink of specific paths.
- Cmd-Shift-3 with multiple monitors saves one screenshot per display, all sharing one second-granularity timestamp. The script never overwrites: moves check the destination first (`safe_move`), and same-timestamp screenshots get `--1`, `--2`, ... suffixes (the first file of a burst is retroactively renamed from the plain name to `--1`).

## Commands

Run the tests:
```zsh
python3 -m unittest test_watch_and_rename_screenshots -v
```

Run the watcher manually (stop launchd job first):
```zsh
./watch-and-rename-screenshots.py
```

Restart the launchd job (needed to pick up script changes):
```zsh
launchctl kickstart -k gui/$(id -u)/com.justin.macos-screenshot-renamer
```

Manage the launchd job:
```zsh
launchctl load ~/Library/LaunchAgents/com.justin.macos-screenshot-renamer.plist
launchctl unload ~/Library/LaunchAgents/com.justin.macos-screenshot-renamer.plist
launchctl list | grep screenshot
```

Check logs:
```zsh
cat logs/stdout.log
cat logs/stderr.log
```

## Dependencies

- `fswatch` (install via `brew install fswatch`, expected at `/opt/homebrew/bin/fswatch`)
- Python 3 (uses only standard library)
- macOS launchd for background execution

## Hardcoded Paths

The script and plist use absolute paths (required because launchd runs with minimal environment):
- RAW_DIR: `/Users/justin/Utilities/macos-screenshot-renamer/raw-screenshots`
- DEST_DIR: `/Users/justin/Desktop`
- FSWATCH_PATH: `/opt/homebrew/bin/fswatch`

If cloning to a different user/location, update these paths in both `watch-and-rename-screenshots.py` and `com.justin.macos-screenshot-renamer.plist`.

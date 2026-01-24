# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a macOS utility that automatically renames screenshots from macOS's default format (which contains a problematic unicode narrow no-break space U+202F before AM/PM) to a simpler format: `screenshot-yyyy-mm-dd--hh-mm-ss.png`.

The system has three parts:
1. macOS is configured to save screenshots to `raw-screenshots/` instead of Desktop
2. A Python script (`watch-and-rename-screenshots.py`) uses `fswatch` to monitor that directory
3. A launchd job (`com.justin.macos-screenshot-renamer.plist`) keeps the Python script running

## Architecture Notes

The Python script handles several macOS quirks:
- macOS writes screenshots in three stages (temp file → hidden file → final file), so the script filters by filename pattern to only process the final file
- The script uses `lsof` to wait until no process has the file open before moving it
- `fswatch` fires multiple events per file; after moving, subsequent events are ignored via the file-exists check
- launchd jobs cannot READ from ~/Desktop without Full Disk Access, but CAN write to it—hence the two-directory approach

## Commands

Run the watcher manually (stop launchd job first):
```zsh
./watch-and-rename-screenshots.py
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

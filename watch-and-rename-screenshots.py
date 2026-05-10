#!/usr/bin/python3
"""
Screenshot Renamer - watches for new macOS screenshots and renames them.

WHY THIS IS COMPLICATED:

1. macOS screenshot filenames contain U+202F (narrow no-break space) before AM/PM,
   which causes problems in terminals and scripts. We rename to a simpler format.

2. macOS writes screenshots in stages:
   - First creates temp file: "..Screenshot 2026-01-15 at 6.34.08 AM.png-oQAC"
   - Renames to hidden file: ".Screenshot 2026-01-15 at 6.34.08 AM.png"
   - Renames to final file:  "Screenshot 2026-01-15 at 6.34.08 AM.png"

   fswatch fires events for each stage. We must ignore temp/hidden files and only
   process the final filename (no leading dots).

3. Even after the final rename, the file may still be open for writing. We use
   lsof to wait until no process has the file open before moving it.

4. fswatch fires multiple events for the same file. After we successfully move
   the file, subsequent events will fail the "file exists" check and be skipped.

5. launchd jobs can't READ from ~/Desktop without Full Disk Access, but CAN WRITE
   to it. So we keep raw screenshots outside Desktop and move renamed files in.

6. launchd runs with minimal environment, so we use absolute paths instead of $HOME.
"""

import subprocess
import sys
import re
from datetime import datetime
from pathlib import Path
import time
from typing import Optional, Tuple

RAW_DIR = Path("/Users/justin/Utilities/macos-screenshot-renamer/raw-screenshots")
DEST_DIR = Path("/Users/justin/Desktop")
FSWATCH_PATH = "/opt/homebrew/bin/fswatch"

# Regex to match final screenshot filename (no leading dots)
SCREENSHOT_PATTERN = re.compile(r"/Screenshot [^/]+\.png$")


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp} {message}", flush=True)


def notify(message: str, title: str = "Screenshot renamed") -> None:
    """Send a macOS notification."""
    safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run([
        "osascript", "-e",
        f'display notification "{safe_msg}" with title "{safe_title}"'
    ], capture_output=True)


def check_screencapture_location() -> None:
    """Warn loudly if macOS isn't configured to save screenshots into RAW_DIR.

    A macOS update can silently reset `defaults write com.apple.screencapture
    location`. When that happens, screenshots go straight to ~/Desktop, fswatch
    sees nothing, and the user gets the old non-breaking-space filenames again
    with no obvious indication the renamer is broken.
    """
    result = subprocess.run(
        ["defaults", "read", "com.apple.screencapture", "location"],
        capture_output=True, text=True
    )
    actual_str = result.stdout.strip() if result.returncode == 0 else ""

    expected = RAW_DIR.resolve()
    actual = Path(actual_str).expanduser().resolve() if actual_str else None

    if actual == expected:
        log(f"OK: screencapture location = {actual_str}")
        return

    seen = actual_str if actual_str else "(unset, defaults to ~/Desktop)"
    problem = f"Default screenshot location is {seen}, not {RAW_DIR}"
    log(f"WARNING: {problem}")
    log("Screenshots will NOT be renamed. Fix with: defaults write com.apple.screencapture location " + str(RAW_DIR) + " && killall SystemUIServer")
    notify(
        f"{problem}. See ~/Utilities/macos-screenshot-renamer/",
        title="Screenshot renamer broken",
    )


def wait_for_file_closed(filepath: Path, max_attempts: int = 50) -> bool:
    """
    Wait until no process has the file open.

    Returns True if file is closed, False if we gave up waiting.
    """
    for attempt in range(max_attempts):
        result = subprocess.run(
            ["lsof", str(filepath)],
            capture_output=True
        )
        if result.returncode != 0:
            # lsof returns non-zero when no process has the file open
            return True
        time.sleep(0.1)

    return False


def get_file_timestamp(filepath: Path) -> str:
    """Get file modification time formatted for the new filename."""
    mtime = filepath.stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d--%H-%M-%S")


def parse_fswatch_line(line: str) -> Optional[Tuple[str, str]]:
    """
    Parse fswatch -x output into (filepath, events).

    Format: "/path/to/file.png Flag1 Flag2 Flag3"

    Returns None if not a .png file.
    """
    # Find where .png ends and flags begin
    match = re.search(r"\.png ", line)
    if not match:
        return None

    split_pos = match.end() - 1  # Position of space after .png
    filepath = line[:split_pos]
    events = line[split_pos + 1:]

    return filepath, events


def process_event(filepath: str, events: str) -> None:
    """Process a single fswatch event."""
    path = Path(filepath)

    # Only process final filenames (no leading dots in basename)
    if not SCREENSHOT_PATTERN.search(filepath):
        return

    # File must still exist (might have been moved by earlier event)
    if not path.exists():
        return

    log(f"Processing: {path.name}")

    # Wait for file to be fully written
    if not wait_for_file_closed(path):
        log(f"ERROR: File still open after max attempts: {path.name}")
        return

    # Generate new filename from file's modification time
    timestamp = get_file_timestamp(path)
    new_name = f"screenshot-{timestamp}.png"
    dest_path = DEST_DIR / new_name

    # Move the file
    try:
        path.rename(dest_path)
        log(f"Renamed: {new_name}")
        notify(new_name)
    except OSError as e:
        log(f"ERROR: Failed to move {path.name}: {e}")


def main() -> None:
    log("Starting screenshot watcher")
    log(f"Watching: {RAW_DIR}")
    log(f"Destination: {DEST_DIR}")
    check_screencapture_location()

    # Start fswatch with:
    #   -0: null-terminated output (handles filenames with spaces/newlines)
    #   -x: include event flags (so we can log what's happening)
    process = subprocess.Popen(
        [FSWATCH_PATH, "-0", "-x", str(RAW_DIR)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Read null-terminated lines from fswatch
    buffer = b""
    while True:
        chunk = process.stdout.read(1)
        if not chunk:
            break

        if chunk == b"\x00":
            line = buffer.decode("utf-8")
            buffer = b""

            parsed = parse_fswatch_line(line)
            if parsed:
                filepath, events = parsed
                log(f"Event: {events} | File: {Path(filepath).name}")
                process_event(filepath, events)
        else:
            buffer += chunk

    log("Watcher exited unexpectedly")
    sys.exit(1)


if __name__ == "__main__":
    main()

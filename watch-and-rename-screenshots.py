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

7. Cmd-Shift-3 with multiple monitors saves one screenshot per display, all with
   the same second-granularity timestamp. They must not overwrite each other, so
   same-timestamp screenshots get --1, --2, ... suffixes, and every move checks
   that the destination doesn't already exist (rename() silently replaces
   existing files). TCC does allow launchd jobs to stat/rename specific
   ~/Desktop paths; it only blocks listing the directory and reading or
   hard-linking files in it.

8. The `com.apple.screencapture` defaults this whole scheme depends on
   (`location`: where screenshots are saved; `target`: whether they are saved
   to disk at all) can be reset while the watcher is running (see
   check_screencapture_settings's docstring for the known causes), so they
   are re-checked every LOCATION_RECHECK_SECONDS in a daemon thread, not just
   at startup.
"""

import subprocess
import sys
import re
import threading
from datetime import datetime
from pathlib import Path
import time
from typing import Optional, Tuple

RAW_DIR = Path("/Users/justin/Utilities/macos-screenshot-renamer/raw-screenshots")
DEST_DIR = Path("/Users/justin/Desktop")
FSWATCH_PATH = "/opt/homebrew/bin/fswatch"
LOCATION_RECHECK_SECONDS = 300

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


def read_screencapture_default(key: str) -> str:
    """Return a com.apple.screencapture default as a string, or "" if unset."""
    result = subprocess.run(
        ["defaults", "read", "com.apple.screencapture", key],
        capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def screencapture_location_ok() -> bool:
    """Quietly report whether the screenshot save folder is RAW_DIR."""
    actual_str = read_screencapture_default("location")
    if not actual_str:
        return False
    return Path(actual_str).expanduser().resolve() == RAW_DIR.resolve()


def screencapture_target_ok() -> bool:
    """Quietly report whether screenshots are saved to disk at all.

    The `target` default selects what happens to a capture: "file" (or
    unset) writes it into `location`; "preview", "clipboard", "mail", and
    "messages" write no file anywhere, so the renamer sees nothing.
    QuickTime's "Save To > QuickTime Player" sets target=preview as a side
    effect, making Cmd-Shift-3 open Preview instead of saving.
    """
    return read_screencapture_default("target") in ("", "file")


def screencapture_settings_ok() -> bool:
    """Quietly report whether screenshots will land in RAW_DIR as files."""
    return screencapture_location_ok() and screencapture_target_ok()


def warn_screencapture_settings_broken() -> None:
    """Log and notify that screenshots are no longer landing in RAW_DIR."""
    problems = []
    fixes = []
    if not screencapture_location_ok():
        seen = read_screencapture_default("location") or "(unset, defaults to ~/Desktop)"
        problems.append(f"screenshot location is {seen}, not {RAW_DIR}")
        fixes.append(f"defaults write com.apple.screencapture location {RAW_DIR}")
    if not screencapture_target_ok():
        seen = read_screencapture_default("target")
        problems.append(f"screenshot target is '{seen}', so screenshots are not saved to disk")
        fixes.append("defaults write com.apple.screencapture target file")
    problem = "; ".join(problems)
    log(f"WARNING: {problem}")
    log("Screenshots will NOT be renamed. Fix with: " + " && ".join(fixes + ["killall SystemUIServer"]))
    notify(
        f"{problem}. See ~/Utilities/macos-screenshot-renamer/",
        title="Screenshot renamer broken",
    )


def check_screencapture_settings() -> bool:
    """Warn loudly if macOS isn't configured to save screenshots into RAW_DIR.

    The screencapture defaults get reset behind our back, with no visible
    sign:

    - A macOS update can silently clear `location`.
    - QuickTime's screen-recording "Options > Save To" menu edits these same
      defaults the instant a menu item is clicked -- no recording needed, and
      clicking an item that already appears selected still writes. "Desktop"
      and "QuickTime Player" are stored by DELETING the `location` key, and
      "QuickTime Player" additionally sets `target=preview` (screenshots open
      in Preview, no file saved). The menu can't display a custom path like
      RAW_DIR, so it misleadingly shows "QuickTime Player" as selected while
      our value is in effect. (Verified experimentally 2026-07-11.)

    When that happens, screenshots go straight to ~/Desktop or stop being
    saved at all, fswatch sees nothing, and the user gets no obvious
    indication the renamer is broken.
    """
    if screencapture_settings_ok():
        log(f"OK: screencapture target=file, location = {RAW_DIR}")
        return True
    warn_screencapture_settings_broken()
    return False


def monitor_screencapture_settings() -> None:
    """Re-check the screencapture settings every few minutes, forever.

    A startup-only check misses resets that happen while the watcher runs
    (QuickTime's Save To menu -- see check_screencapture_settings), leaving
    the renamer silently broken until the next reboot. Runs in a daemon
    thread; warns once per transition to broken, not on every interval.
    """
    was_ok = True
    while True:
        time.sleep(LOCATION_RECHECK_SECONDS)
        ok = screencapture_settings_ok()
        if ok and not was_ok:
            log("OK: screencapture settings restored")
        elif not ok and was_ok:
            warn_screencapture_settings_broken()
        was_ok = ok


def check_fswatch_installed() -> None:
    """Exit loudly if fswatch is missing (e.g. removed by a Homebrew upgrade).

    Without this, Popen raises FileNotFoundError and launchd respawns the
    script in a fast crash-loop with no user-visible signal. Sleeping before
    exit limits the respawn cycle to one notification per hour, and lets the
    job recover automatically once fswatch is reinstalled.
    """
    if Path(FSWATCH_PATH).exists():
        return
    problem = f"fswatch not found at {FSWATCH_PATH}. Install with: brew install fswatch"
    log(f"ERROR: {problem}")
    notify(problem, title="Screenshot renamer broken")
    time.sleep(3600)
    sys.exit(1)


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


# State for the current same-second "burst" of screenshots. Cmd-Shift-3 with
# multiple monitors saves one file per display, all with the same timestamp,
# so they would all map to the same destination filename.
_burst_timestamp: Optional[str] = None
_burst_count = 0


def safe_move(src: Path, dst: Path) -> None:
    """Move src to dst, raising FileExistsError instead of overwriting dst.

    Path.rename silently replaces an existing destination, so check first.
    Check-then-rename is not atomic, but is safe here: this script is the only
    process that creates these destination names, and it handles events
    sequentially. (An atomic no-clobber move via os.link is not an option:
    macOS TCC lets launchd jobs stat and rename ~/Desktop paths, but blocks
    hard-linking into ~/Desktop and listing it.)
    """
    if dst.exists():
        raise FileExistsError(f"Destination already exists: {dst}")
    src.rename(dst)


def move_to_first_free_suffix(src: Path, timestamp: str) -> Path:
    """Move src to the lowest free screenshot-<timestamp>--N.png name."""
    for n in range(1, 1000):
        dst = DEST_DIR / f"screenshot-{timestamp}--{n}.png"
        try:
            safe_move(src, dst)
            return dst
        except FileExistsError:
            continue
    raise FileExistsError(f"No free suffixed destination name for {src}")


def move_screenshot(path: Path, timestamp: str) -> Path:
    """Move a raw screenshot into DEST_DIR, never overwriting existing files.

    A lone screenshot becomes screenshot-<timestamp>.png. When several
    screenshots share one timestamp (multi-monitor Cmd-Shift-3), the first is
    retroactively renamed to ...--1.png and later ones get --2, --3, ...
    """
    global _burst_timestamp, _burst_count

    if timestamp != _burst_timestamp:
        _burst_timestamp = timestamp
        _burst_count = 0

    plain_dest = DEST_DIR / f"screenshot-{timestamp}.png"

    if _burst_count == 0:
        try:
            safe_move(path, plain_dest)
            _burst_count = 1
            return plain_dest
        except FileExistsError:
            # A same-named file is already on the Desktop (e.g. the script
            # restarted mid-burst). Fall through to suffixed naming.
            pass

    if _burst_count <= 1:
        # The burst just grew beyond one file: give the plain-named first
        # screenshot its --1 suffix so the set reads --1, --2, --3, ...
        try:
            move_to_first_free_suffix(plain_dest, timestamp)
        except FileNotFoundError:
            pass  # first file was already moved or deleted by the user

    dest = move_to_first_free_suffix(path, timestamp)
    _burst_count += 1
    return dest


def parse_fswatch_line(line: str) -> Optional[Tuple[str, str]]:
    """
    Parse fswatch output into (filepath, events).

    fswatch is invoked with --event-flag-separator=| so the event flags form
    a single space-free token: "/path/to/file.png Flag1|Flag2|Flag3". The
    last space in the line therefore separates the path (which may itself
    contain spaces) from the flags.

    Returns None if not a .png file.
    """
    filepath, sep, events = line.rpartition(" ")
    if not sep or not filepath.endswith(".png"):
        return None

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

    # Move the file
    try:
        dest_path = move_screenshot(path, timestamp)
        log(f"Renamed: {dest_path.name}")
        notify(dest_path.name)
    except OSError as e:
        log(f"ERROR: Failed to move {path.name}: {e}")


def main() -> None:
    log("Starting screenshot watcher")
    log(f"Watching: {RAW_DIR}")
    log(f"Destination: {DEST_DIR}")
    check_fswatch_installed()
    check_screencapture_settings()
    threading.Thread(target=monitor_screencapture_settings, daemon=True).start()

    # Start fswatch with:
    #   -0: null-terminated output (handles filenames with spaces/newlines)
    #   -x: include event flags (so we can log what's happening)
    #   --event-flag-separator=|: flags form one space-free token, so the last
    #     space in each record unambiguously separates path from flags
    # stderr is inherited (not piped) so it reaches launchd's stderr.log; a
    # full unread pipe buffer would block fswatch and silently stall events.
    process = subprocess.Popen(
        [FSWATCH_PATH, "-0", "-x", "--event-flag-separator=|", str(RAW_DIR)],
        stdout=subprocess.PIPE,
        stderr=None,
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

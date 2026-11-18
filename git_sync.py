"""
git_sync.py - Automatically commit and push sensor data to GitHub.

Runs independently of main.py.

Usage:
    python3 git_sync.py
    python3 git_sync.py --once
"""

import fcntl
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

INTERVAL_S = 60
GIT_TIMEOUT_S = 60

SYNC_PATHS = ("sensor_data", "plots")

LOG_FILE = BASE_DIR / "git_sync.log"
LOCK_FILE = BASE_DIR / ".git" / "git_sync.lock"

stop_event = threading.Event()


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] git_sync: {message}"

    print(line, flush=True)

    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def git(*args, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )

    return result


def sync():
    """Stage, commit and push new data to GitHub."""

    try:
        # Prevent two sync operations from interfering.
        with open(LOCK_FILE, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)

            try:
                # Stage new and modified CSVs and plots.
                paths = [
                    path for path in SYNC_PATHS
                    if (BASE_DIR / path).exists()
                ]

                if paths:
                    git("add", "-A", "--", *paths)

                # Check for staged changes.
                diff = git(
                    "diff", "--cached", "--quiet",
                    check=False
                )

                if diff.returncode == 1:
                    message = (
                        f"data {datetime.now():%Y-%m-%d %H:%M:%S}"
                    )

                    git("commit", "-m", message)
                    log("New data committed.")

                elif diff.returncode != 0:
                    raise RuntimeError(
                        f"Git diff failed: {diff.stderr}"
                    )

                # Push local commits, including earlier failed pushes.
                pending = git(
                    "rev-list", "--count", "@{u}..HEAD"
                )

                count = int(pending.stdout.strip())

                if count == 0:
                    log("Already synced. No new commits.")
                    return True

                log(f"Pushing {count} commit(s)...")

                git("push")

                log("Successfully pushed to GitHub.")
                return True

            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    except subprocess.TimeoutExpired:
        log("Git operation timed out. Will retry.")

    except subprocess.CalledProcessError as error:
        log(f"Git error: {error.stderr or error}")

    except (OSError, ValueError, RuntimeError) as error:
        log(f"Sync error: {error}")

    return False


def handle_signal(signum, frame):
    stop_event.set()


def main():
    if "--once" in sys.argv:
        sys.exit(0 if sync() else 1)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log(f"Automatic Git syncing every {INTERVAL_S} seconds.")

    while not stop_event.is_set():
        sync()
        stop_event.wait(INTERVAL_S)

    log("Stopping Git sync. Performing final push.")
    sync()


if __name__ == "__main__":
    main()
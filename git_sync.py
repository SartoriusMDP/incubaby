"""
git_sync.py - periodically commit and push sensor_data/ to git.

Runs independently of main.py, so a slow or dead network can never
affect the temperature control loop.

Usage:
    python3 git_sync.py            # loop forever, push every INTERVAL_S
    python3 git_sync.py --once     # sync once and exit
"""

import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "sensor_data"
INTERVAL_S = 600        # push every 10 minutes
GIT_TIMEOUT_S = 60      # kill any git command that hangs this long

stop_event = threading.Event()


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def git(*args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        check=check,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )


def sync():
    try:
        git("add", str(DATA_DIR))

        # Exit code 0 means nothing is staged, so nothing to commit.
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            log("No new data.")
            return

        git("commit", "-m", f"data {datetime.now():%Y-%m-%d %H:%M:%S}")
        git("push")
        log("Data pushed.")

    except subprocess.CalledProcessError as error:
        log(f"Git failed: {error.stderr.strip() or error}")
    except subprocess.TimeoutExpired:
        log("Git timed out (network down?). Will retry next cycle.")


def handle_signal(signum, frame):
    stop_event.set()


def main():
    if "--once" in sys.argv:
        sync()
        return

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log(f"Syncing {DATA_DIR} every {INTERVAL_S} s. Ctrl+C to stop.")
    while not stop_event.is_set():
        sync()
        stop_event.wait(INTERVAL_S)

    log("Stopping; doing a final sync.")
    sync()


if __name__ == "__main__":
    main()
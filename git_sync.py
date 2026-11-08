"""
git_sync.py - commit and push sensor_data/ and plots/ to git.

Runs independently of main.py, so a slow or dead network can never
affect the temperature control loop.

Usage:
    python3 git_sync.py            # loop forever, push every INTERVAL_S
    python3 git_sync.py --once     # sync once and exit

run_all.py also imports sync() and uses its True/False result.
"""

import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "sensor_data"
PLOTS_DIR = BASE_DIR / "plots"
INTERVAL_S = 60        # push every 1 minute
GIT_TIMEOUT_S = 60      # kill any git command that hangs this long

stop_event = threading.Event()


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] git_sync: {message}",
          flush=True)


def git(*args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        check=check,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )


def unpushed_commits():
    """True if local commits exist that the remote doesn't have yet."""
    result = git("rev-list", "--count", "@{u}..HEAD", check=False)
    if result.returncode != 0:
        return True  # no upstream set; let `git push` report the problem
    return int(result.stdout.strip() or 0) > 0


def sync():
    """Commit new data and push. Returns True if everything is pushed."""
    try:
        paths = [str(p) for p in (DATA_DIR, PLOTS_DIR) if p.exists()]
        if paths:
            git("add", *paths)

        # Exit code 1 means something is staged and needs a commit.
        if git("diff", "--cached", "--quiet", check=False).returncode != 0:
            git("commit", "-m", f"data {datetime.now():%Y-%m-%d %H:%M:%S}")

        # Checked separately so a failed push is retried next time.
        if not unpushed_commits():
            log("Nothing to push.")
            return True

        git("push")
        log("Pushed.")
        return True

    except subprocess.CalledProcessError as error:
        log(f"Git failed: {error.stderr.strip() or error}")
    except subprocess.TimeoutExpired:
        log("Git timed out (network down?). Will retry.")
    return False


def handle_signal(signum, frame):
    stop_event.set()


def main():
    if "--once" in sys.argv:
        sys.exit(0 if sync() else 1)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log(f"Syncing every {INTERVAL_S} s. Ctrl+C to stop.")
    while not stop_event.is_set():
        sync()
        stop_event.wait(INTERVAL_S)

    log("Stopping; doing a final sync.")
    sync()


if __name__ == "__main__":
    main()
"""
run_all.py - the whole incubator pipeline, started once at boot.

  1. start git_sync.py in the background (pushes data while running)
  2. run main.py for RUN_DURATION_HOURS (None = until the service is stopped)
  3. stop main.py cleanly (SIGTERM -> all outputs turned OFF)
  4. plot each new run's CSV
  5. final push of data + plots, retrying until the network is up

Usage:
    python3 run_all.py            # use RUN_DURATION_HOURS below
    python3 run_all.py 0.05       # quick test: run for 3 minutes
"""

import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import git_sync

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "sensor_data"

RUN_DURATION_HOURS = 1.5     # None = run until the service is stopped
RESTART_DELAY_S = 10        # wait before restarting main.py after a crash
PUSH_ATTEMPTS = 8           # final push retries (e.g. Wi-Fi still coming up)
PUSH_RETRY_DELAY_S = 30
POWER_OFF_WHEN_DONE = False  # True = shut the Pi down after the final push

stop_requested = threading.Event()


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] run_all: {message}",
          flush=True)


def handle_signal(signum, frame):
    stop_requested.set()


def stop_process(proc, timeout=30):
    """Ask a child to stop with SIGTERM; kill it if it doesn't."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"pid {proc.pid} ignored SIGTERM; killing it")
        proc.kill()
        proc.wait()


def remove_empty_runs(before):
    """Delete header-only CSVs left behind by failed start-up attempts."""
    for path in set(DATA_DIR.glob("run_*.csv")) - before:
        with open(path) as f:
            if sum(1 for _ in f) <= 1:
                path.unlink()
                log(f"Removed empty file {path.name}")


def run_controller(deadline):
    """Run main.py until the deadline, a stop request, or a clean exit.
    If main.py crashes (e.g. sensors not ready yet), restart it."""
    while not stop_requested.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            return

        proc = subprocess.Popen(
            [sys.executable, "-u", str(BASE_DIR / "main.py")],
            cwd=BASE_DIR,
        )
        log(f"main.py started (pid {proc.pid})")

        while proc.poll() is None:
            out_of_time = (
                deadline is not None and time.monotonic() >= deadline
            )
            if stop_requested.is_set() or out_of_time:
                log("Stopping main.py...")
                stop_process(proc)
                return
            stop_requested.wait(1)

        if proc.returncode == 0:
            log("main.py exited normally.")
            return

        log(f"main.py exited with code {proc.returncode}; "
            f"restarting in {RESTART_DELAY_S} s")
        stop_requested.wait(RESTART_DELAY_S)


def plot_runs(paths):
    for path in paths:
        log(f"Plotting {path.name}")
        try:
            subprocess.run(
                [sys.executable, str(BASE_DIR / "plot_sensor_data.py"),
                 str(path)],
                cwd=BASE_DIR,
                check=True,
                timeout=300,
            )
        except (subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as error:
            log(f"Plot failed for {path.name}: {error}")


def final_push():
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        if git_sync.sync():
            return True
        log(f"Push attempt {attempt}/{PUSH_ATTEMPTS} failed; "
            f"retrying in {PUSH_RETRY_DELAY_S} s")
        time.sleep(PUSH_RETRY_DELAY_S)
    log("Giving up. Data is saved locally and will be pushed next run.")
    return False


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    hours = float(sys.argv[1]) if len(sys.argv) > 1 else RUN_DURATION_HOURS
    deadline = None if hours is None else time.monotonic() + hours * 3600

    DATA_DIR.mkdir(exist_ok=True)
    before = set(DATA_DIR.glob("run_*.csv"))

    # Background pusher: periodic pushes while the run is going.
    sync_proc = subprocess.Popen(
        [sys.executable, "-u", str(BASE_DIR / "git_sync.py")],
        cwd=BASE_DIR,
    )

    if hours is None:
        log("Running until stopped.")
    else:
        log(f"Running for {hours} h.")

    try:
        run_controller(deadline)
    finally:
        # git_sync.py does one last sync when it receives SIGTERM.
        stop_process(sync_proc, timeout=90)

    remove_empty_runs(before)
    new_runs = sorted(set(DATA_DIR.glob("run_*.csv")) - before)

    plot_runs(new_runs)
    final_push()

    log("Done.")
    if POWER_OFF_WHEN_DONE and not stop_requested.is_set():
        subprocess.run(["sudo", "-n", "shutdown", "-h", "now"])


if __name__ == "__main__":
    main()
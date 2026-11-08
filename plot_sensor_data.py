"""
plot_sensor_data.py - make temperature / humidity / duty-cycle plots.

Usage:
    python3 plot_sensor_data.py                  # keep plots up to date (live)
    python3 plot_sensor_data.py --once           # plot the newest run and exit
    python3 plot_sensor_data.py sensor_data/run_X.csv   # plot that run and exit

Live mode follows the newest run in sensor_data/, including runs that start
later, and redraws plots/live/ whenever new data arrives. Keep those images
open to watch them update. plots/live/ is git-ignored so git_sync.py doesn't
commit every refresh; the finished plots go to plots/<run name>/ when the run
changes or you press Ctrl+C.
"""

import multiprocessing
import os
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless: no display needed on the Pi
import matplotlib.pyplot as plt
import pandas as pd

# ===== Configuration =====
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "sensor_data"
PLOTS_DIR = BASE_DIR / "plots"
LIVE_DIR = PLOTS_DIR / "live"
TARGET_TEMP_C = 37.5
REFRESH_S = 60  # how often live mode checks for new data

NUMERIC_COLS = [
    "Sensor_Channel",
    "Temperature_C",
    "Humidity_RH",
    "Average_Temperature_C",
    "Heatpad_Duty_Percent",
    "Fan_Duty_Percent",
]

# One fixed color per mux channel, so a sensor keeps its color from run to run
# even when other channels are empty. Checked for color-blind separation.
CHANNEL_COLORS = [
    "#2a78d6",  # 0 blue
    "#eb6834",  # 1 orange
    "#1baf7a",  # 2 aqua
    "#eda100",  # 3 yellow
    "#e87ba4",  # 4 magenta
    "#008300",  # 5 green
    "#6250d6",  # 6 violet
    "#e34948",  # 7 red
]
HEATPAD_COLOR = CHANNEL_COLORS[1]
FAN_COLOR = CHANNEL_COLORS[0]
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"

plt.rcParams.update({
    "figure.facecolor": "#fcfcfb",
    "axes.facecolor": "#fcfcfb",
    "axes.edgecolor": "#c3c2b7",
    "axes.grid": True,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.labelcolor": SECONDARY_INK,
    "grid.color": "#e1e0d9",
    "grid.linewidth": 0.8,
    "text.color": INK,
    "xtick.color": SECONDARY_INK,
    "ytick.color": SECONDARY_INK,
})


# ===== Pick the CSV =====
def has_data(path):
    """False for header-only files (a run just starting, or one that failed)."""
    with open(path, errors="replace") as f:
        f.readline()
        # A power cut can leave NUL bytes in place of the rows.
        return bool(f.readline().strip("\0 \r\n"))


def newest_run():
    """Newest run that has at least one row of data, or None."""
    for path in sorted(DATA_DIR.glob("run_*.csv"), reverse=True):
        if has_data(path):
            return path
    return None


# ===== Load CSV =====
def load_run(csv_file):
    """Return (per-sensor data, per-timestamp data, last reading time)."""
    df = pd.read_csv(csv_file, on_bad_lines="skip")
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["Timestamp"]).sort_values("Timestamp")

    if df.empty:
        raise ValueError(f"No rows with a valid timestamp in {csv_file}")

    # Coerce numeric columns so things like "ERR", "nan" or blanks become NaN
    # instead of breaking the aggregation.
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # One time origin for every plot, taken from the full log
    # (not just rows that have valid sensor readings).
    t0 = df["Timestamp"].min()
    df["Elapsed_Minutes"] = (df["Timestamp"] - t0).dt.total_seconds() / 60

    # Per-sensor data (only rows with a real channel and reading).
    valid_sensor_rows = df.dropna(subset=["Sensor_Channel"])
    valid_sensor_rows = valid_sensor_rows[
        valid_sensor_rows["Temperature_C"].notna()
        | valid_sensor_rows["Humidity_RH"].notna()
    ]
    # A blank row makes pandas read channels as 0.0, 1.0, ...
    valid_sensor_rows = valid_sensor_rows.astype({"Sensor_Channel": int})

    if "Sensor_Name" in df.columns:
        names = (
            valid_sensor_rows.drop_duplicates("Sensor_Channel")
            .set_index("Sensor_Channel")["Sensor_Name"]
            .to_dict()
        )
    else:
        names = {}

    sensor_data = (
        valid_sensor_rows.groupby(["Timestamp", "Sensor_Channel"], as_index=False)
        .agg({
            "Elapsed_Minutes": "first",
            "Temperature_C": "mean",
            "Humidity_RH": "mean",
        })
    )

    # Per-timestamp data (from the full log, independent of sensors).
    per_timestamp = (
        df.groupby("Timestamp", as_index=False)
        .agg({
            "Elapsed_Minutes": "first",
            "Average_Temperature_C": "mean",
            "Heatpad_Duty_Percent": "mean",
            "Fan_Duty_Percent": "mean",
        })
    )

    return sensor_data, per_timestamp, df["Timestamp"].max()


# ===== Drawing helpers =====
def plot_sensors(ax, sensor_data, column):
    for channel, group in sensor_data.groupby("Sensor_Channel"):
        ax.plot(
            group["Elapsed_Minutes"],
            group[column],
            color=CHANNEL_COLORS[channel % len(CHANNEL_COLORS)],
            linewidth=1.2,
            label=names.get(channel, f"Sensor {channel}"),
        )


def finish_plot(fig, ax, title, subtitle, ylabel, path):
    ax.set_title(f"{title}\n", loc="left", fontweight="bold")
    ax.text(0, 1.02, subtitle, transform=ax.transAxes,
            color=SECONDARY_INK, fontsize=9)
    ax.set_xlabel("Elapsed Time (minutes)")
    ax.set_ylabel(ylabel)
    if ax.get_legend_handles_labels()[0]:
        # Outside the axes so up to 8 sensors never cover the data.
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    fig.tight_layout()

    # Write then rename, so an open image viewer or git_sync.py never sees a
    # half-written PNG. The temp file lives in the git-ignored live folder.
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LIVE_DIR / f"{os.getpid()}_{path.name}.tmp"
    fig.savefig(tmp, dpi=200, format="png")
    tmp.replace(path)
    plt.close(fig)


# ===== The three plots =====
def draw_temperature(sensor_data, per_timestamp, subtitle, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    plot_sensors(ax, sensor_data, "Temperature_C")

    avg = per_timestamp.dropna(subset=["Average_Temperature_C"])
    if not avg.empty:
        ax.plot(
            avg["Elapsed_Minutes"],
            avg["Average_Temperature_C"],
            color=INK,
            linewidth=2.5,
            label="Average Temperature",
        )
    else:
        print("Warning: Average_Temperature_C has no valid values.")

    ax.axhline(
        TARGET_TEMP_C,
        color=SECONDARY_INK,
        linestyle="--",
        linewidth=1.5,
        label=f"Target ({TARGET_TEMP_C}°C)",
    )
    finish_plot(fig, ax, "SHT45 Temperature vs. Time", subtitle,
                "Temperature (°C)", path)


def draw_humidity(sensor_data, per_timestamp, subtitle, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    if not sensor_data.empty:
        plot_sensors(ax, sensor_data, "Humidity_RH")
    else:
        ax.text(0.5, 0.5, "No valid humidity readings",
                ha="center", va="center", transform=ax.transAxes)
    finish_plot(fig, ax, "SHT45 Relative Humidity vs. Time", subtitle,
                "Relative Humidity (% RH)", path)


def draw_actuator_duty(sensor_data, per_timestamp, subtitle, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(
        per_timestamp["Elapsed_Minutes"],
        per_timestamp["Heatpad_Duty_Percent"],
        color=HEATPAD_COLOR,
        linewidth=1.5,
        label="Heatpad",
    )
    ax.plot(
        per_timestamp["Elapsed_Minutes"],
        per_timestamp["Fan_Duty_Percent"],
        color=FAN_COLOR,
        linewidth=1.5,
        label="Fan",
    )
    ax.set_ylim(-5, 105)
    finish_plot(fig, ax, "Actuator Duty Cycle vs. Time", subtitle,
                "Duty Cycle (%)", path)


PLOTS = [
    (draw_temperature, "temperature.png"),
    (draw_humidity, "humidity.png"),
    (draw_actuator_duty, "actuator_duty.png"),
]


def ignore_stop_signals():
    """Workers finish their plot; the main process decides when to stop."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def render(csv_file, output_dir):
    sensor_data, per_timestamp, last_reading = load_run(csv_file)
    if sensor_data.empty:
        print("Warning: no valid per-sensor readings found; "
              "plotting average and actuator data only.")

    output_dir.mkdir(parents=True, exist_ok=True)
    subtitle = f"{csv_file.stem}  |  last reading {last_reading:%H:%M:%S}"

    # Rendering and saving the PNGs is most of the run time, so each plot gets
    # its own core. "fork" workers inherit the imports, so they start instantly.
    with ProcessPoolExecutor(
        max_workers=len(PLOTS),
        mp_context=multiprocessing.get_context("fork"),
        initializer=ignore_stop_signals,
    ) as pool:
        futures = [
            pool.submit(draw, sensor_data, per_timestamp, subtitle,
                        output_dir / filename)
            for draw, filename in PLOTS
        ]
        for future in futures:
            future.result()  # re-raises any error from the worker


def save_finished(csv_file):
    """Save a run's plots to its own folder, so runs never overwrite each other."""
    output_dir = PLOTS_DIR / csv_file.stem
    render(csv_file, output_dir)
    print(f"Saved to: {output_dir}", flush=True)


# ===== Live mode =====
def follow():
    print(f"Live plots: {LIVE_DIR}\n"
          f"Checking for new data every {REFRESH_S} s. Ctrl+C to stop.",
          flush=True)

    current = None      # the run being followed
    last_version = None  # (path, modified time) of the last successful draw

    try:
        while True:
            try:
                latest = newest_run()
                if latest is None:
                    if current is None:
                        print("Waiting for data...", flush=True)
                else:
                    if latest != current:
                        # Switch first: a bad old run must not block the new one.
                        previous, current = current, latest
                        print(f"Following {current.name}", flush=True)
                        if previous is not None:
                            save_finished(previous)

                    version = (current, current.stat().st_mtime_ns)
                    if version != last_version:
                        render(current, LIVE_DIR)
                        last_version = version

            # Damaged or disappearing files must not end the live view;
            # the next check tries again.
            except (ValueError, OSError, BrokenProcessPool) as error:
                print(f"Plot skipped: {error}", flush=True)

            time.sleep(REFRESH_S)

    except KeyboardInterrupt:
        print("\nStopping...", flush=True)

    finally:
        if current is not None:
            try:
                save_finished(current)
            except (ValueError, OSError, BrokenProcessPool) as error:
                print(f"Could not save {current.name}: {error}", flush=True)


def main():
    # Make `kill` and shutdown save the finished plots too.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

    args = [arg for arg in sys.argv[1:] if arg != "--once"]

    if args:
        csv_file = Path(args[0])
        if not csv_file.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_file}")
    elif "--once" in sys.argv:
        csv_file = newest_run()
        if csv_file is None:
            raise FileNotFoundError("No run_*.csv files with data in sensor_data/")
    else:
        follow()
        return

    print(f"Plotting {csv_file}")
    save_finished(csv_file)
    print("Plots generated successfully!")


if __name__ == "__main__":
    main()

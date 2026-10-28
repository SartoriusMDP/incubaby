"""
Live plot for SHT45 / heatpad / fan logger CSVs.

Watches a CSV while the logger is writing to it and redraws every few
seconds. Only newly appended rows are read on each update, so it stays
fast even on long runs.

Usage:
    python live_plot.py                      # newest CSV in sensor_data/
    python live_plot.py --file sensor_data/run_2026-10-24_15-17-47.csv
    python live_plot.py --window 10          # show only the last 10 minutes
    python live_plot.py --interval 1         # refresh every second

Closing the window saves a snapshot of the final plot to plots/.
"""

import argparse
import io
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# ===== Defaults =====
DATA_DIR = Path("sensor_data")
TARGET_TEMP_C = 37.5
OUTPUT_DIR = Path("plots")

NUMERIC_COLS = [
    "Temperature_C",
    "Humidity_RH",
    "Average_Temperature_C",
    "Heatpad_Duty_Percent",
    "Fan_Duty_Percent",
]


def newest_csv(folder: Path) -> Path:
    files = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"No CSV files found in {folder}")
    return files[-1]


class CsvTail:
    """Reads only rows appended to a CSV since the last call."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.header = None
        self.partial = ""  # incomplete last line from the previous read

    def read_new(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()

        size = self.path.stat().st_size
        if size < self.offset:  # file was truncated / replaced: start over
            self.offset, self.header, self.partial = 0, None, ""

        with open(self.path, "r", newline="") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()

        text = self.partial + chunk
        if not text:
            return pd.DataFrame()

        # Hold back a line the logger hasn't finished writing yet.
        if not text.endswith("\n"):
            text, _, self.partial = text.rpartition("\n")
            if not text:
                return pd.DataFrame()
            text += "\n"
        else:
            self.partial = ""

        if self.header is None:
            self.header, _, text = text.partition("\n")
            if not text.strip():
                return pd.DataFrame()

        try:
            return pd.read_csv(io.StringIO(self.header + "\n" + text))
        except pd.errors.ParserError:
            # A corrupted line shouldn't kill the live view.
            return pd.read_csv(
                io.StringIO(self.header + "\n" + text), on_bad_lines="skip"
            )


def normalize_channel(s):
    """Keep channel IDs as clean ints (0, 1) even when some rows are blank."""
    num = pd.to_numeric(s, errors="coerce")
    if num[s.notna()].notna().all() and (num.dropna() % 1 == 0).all():
        return num.astype("Int64")
    return s


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["Timestamp"])
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Sensor_Channel" in df.columns:
        df["Sensor_Channel"] = normalize_channel(df["Sensor_Channel"])
    return df


def build_views(df: pd.DataFrame):
    """Return (per-sensor data, per-timestamp data), same logic as the static script."""
    df = df.sort_values("Timestamp")
    t0 = df["Timestamp"].min()
    df["Elapsed_Minutes"] = (df["Timestamp"] - t0).dt.total_seconds() / 60

    valid = df.dropna(subset=["Sensor_Channel"])
    valid = valid[valid["Temperature_C"].notna() | valid["Humidity_RH"].notna()]
    sensor_data = (
        valid.groupby(["Timestamp", "Sensor_Channel"], as_index=False)
        .agg({
            "Elapsed_Minutes": "first",
            "Temperature_C": "mean",
            "Humidity_RH": "mean",
        })
    )

    per_ts = (
        df.groupby("Timestamp", as_index=False)
        .agg({
            "Elapsed_Minutes": "first",
            "Average_Temperature_C": "mean",
            "Heatpad_Duty_Percent": "mean",
            "Fan_Duty_Percent": "mean",
        })
    )
    return sensor_data, per_ts


def main():
    parser = argparse.ArgumentParser(description="Live plot of logger CSV")
    parser.add_argument("--file", type=Path, help="CSV to watch (default: newest in sensor_data/)")
    parser.add_argument("--interval", type=float, default=2.0, help="refresh period in seconds")
    parser.add_argument("--window", type=float, default=None,
                        help="only show the last N minutes (default: whole run)")
    args = parser.parse_args()

    csv_path = args.file or newest_csv(DATA_DIR)
    print(f"Watching {csv_path}  (Ctrl+C or close the window to stop)")

    tail = CsvTail(csv_path)
    data = pd.DataFrame()

    fig, (ax_t, ax_h, ax_d) = plt.subplots(
        3, 1, figsize=(12, 10), sharex=True,
        gridspec_kw={"height_ratios": [2, 1.2, 1]},
    )
    fig.canvas.manager.set_window_title(f"Live: {csv_path.name}")

    # Fixed lines
    avg_line, = ax_t.plot([], [], linewidth=2.5, color="black", label="Average Temperature")
    ax_t.axhline(TARGET_TEMP_C, linestyle="--", linewidth=1.5, color="red",
                 label=f"Target ({TARGET_TEMP_C}°C)")
    heat_line, = ax_d.plot([], [], linewidth=1.5, label="Heatpad")
    fan_line, = ax_d.plot([], [], linewidth=1.5, label="Fan")

    # Per-sensor lines are created as channels appear
    temp_lines, hum_lines = {}, {}

    ax_t.set_ylabel("Temperature (°C)")
    ax_h.set_ylabel("RH (%)")
    ax_d.set_ylabel("Duty (%)")
    ax_d.set_xlabel("Elapsed Time (minutes)")
    ax_d.set_ylim(-5, 105)
    for ax in (ax_t, ax_h, ax_d):
        ax.grid(True, alpha=0.3)
    ax_d.legend(loc="upper left")

    status = fig.text(0.01, 0.005, "Waiting for data…", fontsize=9, color="gray")

    def update(_frame):
        nonlocal data
        new = tail.read_new()
        if not new.empty:
            data = pd.concat([data, clean(new)], ignore_index=True)
        if data.empty:
            return

        sensor_data, per_ts = build_views(data)

        # --- Per-sensor temperature + humidity ---
        for channel, group in sensor_data.groupby("Sensor_Channel"):
            if channel not in temp_lines:
                temp_lines[channel], = ax_t.plot([], [], marker=".", markersize=3,
                                                 linewidth=1.2, label=f"Sensor {channel}")
                color = temp_lines[channel].get_color()
                hum_lines[channel], = ax_h.plot([], [], marker=".", markersize=3,
                                                linewidth=1.2, color=color,
                                                label=f"Sensor {channel}")
                ax_t.legend(loc="upper left")
                ax_h.legend(loc="upper left")
            temp_lines[channel].set_data(group["Elapsed_Minutes"], group["Temperature_C"])
            hum_lines[channel].set_data(group["Elapsed_Minutes"], group["Humidity_RH"])

        if not temp_lines and not ax_t.get_legend():
            ax_t.legend(loc="upper left")

        # --- Average + actuators (plot even with no valid sensor data) ---
        avg = per_ts.dropna(subset=["Average_Temperature_C"])
        avg_line.set_data(avg["Elapsed_Minutes"], avg["Average_Temperature_C"])
        heat_line.set_data(per_ts["Elapsed_Minutes"], per_ts["Heatpad_Duty_Percent"])
        fan_line.set_data(per_ts["Elapsed_Minutes"], per_ts["Fan_Duty_Percent"])

        # --- Axes limits ---
        t_max = per_ts["Elapsed_Minutes"].max()
        t_min = max(0.0, t_max - args.window) if args.window else 0.0
        ax_d.set_xlim(t_min, max(t_max, t_min + 0.1))
        for ax in (ax_t, ax_h):
            ax.relim(visible_only=True)
            ax.autoscale_view(scalex=False, scaley=True)

        # --- Title / status line with latest values ---
        last = per_ts.iloc[-1]
        avg_txt = (f"{last['Average_Temperature_C']:.2f} °C"
                   if pd.notna(last["Average_Temperature_C"]) else "n/a")
        ax_t.set_title(f"Live — avg {avg_txt}  |  heat {last['Heatpad_Duty_Percent']:.0f}%  "
                       f"|  fan {last['Fan_Duty_Percent']:.0f}%")
        status.set_text(f"{len(data)} rows  •  last update "
                        f"{last['Timestamp']:%H:%M:%S}  •  {t_max:.1f} min elapsed"
                        + ("" if temp_lines else "  •  no valid sensor readings yet"))

    def on_close(_event):
        if not data.empty:
            OUTPUT_DIR.mkdir(exist_ok=True)
            out = OUTPUT_DIR / f"live_{csv_path.stem}.png"
            fig.savefig(out, dpi=200)
            print(f"Saved snapshot to {out.resolve()}")

    fig.canvas.mpl_connect("close_event", on_close)
    update(0)
    fig.tight_layout(rect=(0, 0.02, 1, 1))

    # Keep a reference so the animation isn't garbage-collected
    anim = FuncAnimation(fig, update, interval=args.interval * 1000,
                         cache_frame_data=False)
    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    return anim


if __name__ == "__main__":
    main()
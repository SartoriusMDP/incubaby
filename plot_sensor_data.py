import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# ===== Configuration =====
CSV_FILE = Path("sensor_data/run_2026-10-28_19-02-56.csv")
TARGET_TEMP_C = 37.5

NUMERIC_COLS = [
    "Temperature_C",
    "Humidity_RH",
    "Average_Temperature_C",
    "Heatpad_Duty_Percent",
    "Fan_Duty_Percent",
]

# ===== Load CSV =====
if not CSV_FILE.exists():
    raise FileNotFoundError(f"CSV file not found: {CSV_FILE}")

df = pd.read_csv(CSV_FILE)
df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
df = df.dropna(subset=["Timestamp"]).sort_values("Timestamp")

if df.empty:
    raise ValueError(f"No rows with a valid timestamp in {CSV_FILE}")

# Coerce numeric columns so things like "ERR", "nan" or blanks become NaN
# instead of breaking the aggregation.
for col in NUMERIC_COLS:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

# One time origin for every plot, taken from the full log
# (not just rows that have valid sensor readings).
t0 = df["Timestamp"].min()
df["Elapsed_Minutes"] = (df["Timestamp"] - t0).dt.total_seconds() / 60

# ===== Per-sensor data (only rows with a real channel and reading) =====
valid_sensor_rows = df.dropna(subset=["Sensor_Channel"])
valid_sensor_rows = valid_sensor_rows[
    valid_sensor_rows["Temperature_C"].notna()
    | valid_sensor_rows["Humidity_RH"].notna()
]

sensor_data = (
    valid_sensor_rows.groupby(["Timestamp", "Sensor_Channel"], as_index=False)
    .agg({
        "Elapsed_Minutes": "first",
        "Temperature_C": "mean",
        "Humidity_RH": "mean",
    })
)

has_sensor_data = not sensor_data.empty
if not has_sensor_data:
    print("Warning: no valid per-sensor readings found; "
          "plotting average and actuator data only.")

# ===== Per-timestamp data (from the full log, independent of sensors) =====
per_timestamp = (
    df.groupby("Timestamp", as_index=False)
    .agg({
        "Elapsed_Minutes": "first",
        "Average_Temperature_C": "mean",
        "Heatpad_Duty_Percent": "mean",
        "Fan_Duty_Percent": "mean",
    })
)

# Create output folder
output_dir = Path("plots")
output_dir.mkdir(exist_ok=True)

# ===== Plot 1: Temperature =====
fig, ax = plt.subplots(figsize=(12, 6))

if has_sensor_data:
    for channel, group in sensor_data.groupby("Sensor_Channel"):
        ax.plot(
            group["Elapsed_Minutes"],
            group["Temperature_C"],
            marker=".",
            markersize=3,
            linewidth=1.2,
            label=f"Sensor {channel}",
        )

avg = per_timestamp.dropna(subset=["Average_Temperature_C"])
if not avg.empty:
    ax.plot(
        avg["Elapsed_Minutes"],
        avg["Average_Temperature_C"],
        linewidth=2.5,
        label="Average Temperature",
    )
else:
    print("Warning: Average_Temperature_C has no valid values.")

ax.axhline(
    TARGET_TEMP_C,
    linestyle="--",
    linewidth=1.5,
    label=f"Target ({TARGET_TEMP_C}°C)",
)

ax.set_title("SHT45 Temperature vs. Time")
ax.set_xlabel("Elapsed Time (minutes)")
ax.set_ylabel("Temperature (°C)")
ax.grid(True, alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig(output_dir / "temperature.png", dpi=200)
plt.close(fig)

# ===== Plot 2: Humidity =====
fig, ax = plt.subplots(figsize=(12, 6))

if has_sensor_data:
    for channel, group in sensor_data.groupby("Sensor_Channel"):
        ax.plot(
            group["Elapsed_Minutes"],
            group["Humidity_RH"],
            marker=".",
            markersize=3,
            linewidth=1.2,
            label=f"Sensor {channel}",
        )
    ax.legend()
else:
    ax.text(0.5, 0.5, "No valid humidity readings",
            ha="center", va="center", transform=ax.transAxes)

ax.set_title("SHT45 Relative Humidity vs. Time")
ax.set_xlabel("Elapsed Time (minutes)")
ax.set_ylabel("Relative Humidity (% RH)")
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(output_dir / "humidity.png", dpi=200)
plt.close(fig)

# ===== Plot 3: Heatpad and Fan Duty Cycle =====
fig, ax = plt.subplots(figsize=(12, 6))

ax.plot(
    per_timestamp["Elapsed_Minutes"],
    per_timestamp["Heatpad_Duty_Percent"],
    linewidth=1.5,
    label="Heatpad",
)

ax.plot(
    per_timestamp["Elapsed_Minutes"],
    per_timestamp["Fan_Duty_Percent"],
    linewidth=1.5,
    label="Fan",
)

ax.set_title("Actuator Duty Cycle vs. Time")
ax.set_xlabel("Elapsed Time (minutes)")
ax.set_ylabel("Duty Cycle (%)")
ax.set_ylim(-5, 105)
ax.grid(True, alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig(output_dir / "actuator_duty.png", dpi=200)
plt.close(fig)

print("Plots generated successfully!")
print(f"Saved to: {output_dir.resolve()}")
print("- temperature.png")
print("- humidity.png")
print("- actuator_duty.png")
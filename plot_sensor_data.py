import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# ===== Configuration =====
CSV_FILE = Path("sensor_data/run_2026-10-18_19-05-19.csv")
TARGET_TEMP_C = 37.5

# ===== Load CSV =====
if not CSV_FILE.exists():
    raise FileNotFoundError(f"CSV file not found: {CSV_FILE}")

df = pd.read_csv(CSV_FILE)
df["Timestamp"] = pd.to_datetime(df["Timestamp"])

# Sort records chronologically
df = df.sort_values("Timestamp")

# Convert time to elapsed seconds for a readable x-axis
df["Elapsed_Minutes"] = (
    df["Timestamp"] - df["Timestamp"].min()
).dt.total_seconds() / 60

# Remove duplicate timestamps within each sensor by averaging them.
# This is useful if the logger records several sensors in the same loop.
sensor_data = (
    df.groupby(["Timestamp", "Sensor_Channel"], as_index=False)
    .agg({
        "Temperature_C": "mean",
        "Humidity_RH": "mean",
        "Average_Temperature_C": "mean",
        "Heatpad_Duty_Percent": "mean",
        "Fan_Duty_Percent": "mean",
    })
)

sensor_data["Elapsed_Minutes"] = (
    sensor_data["Timestamp"] - sensor_data["Timestamp"].min()
).dt.total_seconds() / 60

# Create output folder
output_dir = Path("plots")
output_dir.mkdir(exist_ok=True)

# ===== Plot 1: Temperature =====
fig, ax = plt.subplots(figsize=(12, 6))

for channel, group in sensor_data.groupby("Sensor_Channel"):
    ax.plot(
        group["Elapsed_Minutes"],
        group["Temperature_C"],
        marker=".",
        markersize=3,
        linewidth=1.2,
        label=f"Sensor {channel}",
    )

# Plot the average temperature once per timestamp
avg = (
    sensor_data.groupby("Timestamp", as_index=False)
    .agg({
        "Elapsed_Minutes": "mean",
        "Average_Temperature_C": "mean",
    })
)

ax.plot(
    avg["Elapsed_Minutes"],
    avg["Average_Temperature_C"],
    linewidth=2.5,
    label="Average Temperature",
)

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

for channel, group in sensor_data.groupby("Sensor_Channel"):
    ax.plot(
        group["Elapsed_Minutes"],
        group["Humidity_RH"],
        marker=".",
        markersize=3,
        linewidth=1.2,
        label=f"Sensor {channel}",
    )

ax.set_title("SHT45 Relative Humidity vs. Time")
ax.set_xlabel("Elapsed Time (minutes)")
ax.set_ylabel("Relative Humidity (% RH)")
ax.grid(True, alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig(output_dir / "humidity.png", dpi=200)
plt.close(fig)

# ===== Plot 3: Heatpad and Fan Duty Cycle =====
actuators = (
    sensor_data.groupby("Timestamp", as_index=False)
    .agg({
        "Elapsed_Minutes": "mean",
        "Heatpad_Duty_Percent": "mean",
        "Fan_Duty_Percent": "mean",
    })
)

fig, ax = plt.subplots(figsize=(12, 6))

ax.plot(
    actuators["Elapsed_Minutes"],
    actuators["Heatpad_Duty_Percent"],
    linewidth=1.5,
    label="Heatpad",
)

ax.plot(
    actuators["Elapsed_Minutes"],
    actuators["Fan_Duty_Percent"],
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
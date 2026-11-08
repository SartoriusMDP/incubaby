import csv
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import board
import adafruit_sht4x
import adafruit_tca9548a
from gpiozero import PWMOutputDevice, OutputDevice
from simple_pid import PID

# Make "systemctl stop" / shutdown run the finally: block below,
# so all outputs are turned OFF cleanly.
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

# ---------------------------------------------------------
# 1. CSV DATA LOGGING - NEW FILE FOR EACH RUN
# ---------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "sensor_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Record the start time of this run.
START_TIME = datetime.now()
RUN_ID = START_TIME.strftime("%Y-%m-%d_%H-%M-%S")

CSV_FILE = DATA_DIR / f"run_{RUN_ID}.csv"

# Create a new CSV file for this run.
with open(CSV_FILE, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "Timestamp",
        "Sensor_Channel",
        "Sensor_Name",
        "Temperature_C",
        "Temperature_F",
        "Humidity_RH",
        "Average_Temperature_C",
        "Heatpad_Duty_Percent",
        "Fan_Duty_Percent",
    ])

print(f"Run started at: {START_TIME.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Logging sensor data to: {CSV_FILE}")

# ---------------------------------------------------------
# 2. SENSOR INITIALIZATION  (2 x TCA9548A = up to 16 sensors)
# ---------------------------------------------------------
MUX_ADDRESSES = [0x70, 0x71]  # 0x71 = A0 jumper bridged on the second mux

SENSOR_NAMES = {
    0: "Right Side Box  ",
    1: "Left Side Box   ",
    2: "Back Cell Tray  ",
    3: "Right Cell Tray ",
    4: "Left Cell Tray  ",
    5: "Top             ",
    6: "Bottom Left     ",
    7: "Bottom Right    ",
    8: "Bottom Box      ",
    9: "Ambient         "
    # 5 ... 15: add as you wire them
}

def sensor_name(sensor_id):
    return SENSOR_NAMES.get(sensor_id, f"Sensor {sensor_id}")

CHANNELS_PER_MUX = 8
MAX_SENSORS = len(MUX_ADDRESSES) * CHANNELS_PER_MUX  # 16
DISCOVERY_RETRIES = 3         # attempts per mux channel

# Sensor IDs run 0..15:  ID = mux_index * 8 + channel
#   mux 0x70, channels 0-7  ->  IDs 0-7   (same IDs as before)
#   mux 0x71, channels 0-7  ->  IDs 8-15

i2c = board.I2C()


def reset_mux(address):
    """Deselect every channel on one mux. Also works as a presence
    check: returns False if nothing answers at that address."""
    while not i2c.try_lock():
        time.sleep(0)
    try:
        i2c.writeto(address, bytes([0]))
        return True
    except (OSError, RuntimeError):
        return False
    finally:
        i2c.unlock()


def release_bus():
    """A failed transfer can leak the I2C lock and leave a mux channel
    selected. Release the lock and deselect all channels on both muxes,
    so two sensors (both at 0x44) are never active at the same time."""
    try:
        i2c.unlock()
    except Exception:
        pass
    for address in MUX_ADDRESSES:
        reset_mux(address)


print("Scanning mux channels for SHT45 sensors...", flush=True)

sensors = {}  # sensor ID -> SHT4x object

for mux_index, address in enumerate(MUX_ADDRESSES):
    if not reset_mux(address):
        print(
            f"Mux {mux_index} (0x{address:02X}): not responding, "
            f"skipping its {CHANNELS_PER_MUX} channels",
            flush=True,
        )
        continue

    mux = adafruit_tca9548a.TCA9548A(i2c, address=address)

    for channel in range(CHANNELS_PER_MUX):
        sensor_id = mux_index * CHANNELS_PER_MUX + channel

        for attempt in range(DISCOVERY_RETRIES):
            try:
                sht = adafruit_sht4x.SHT4x(mux[channel])
                sht.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION
                sensors[sensor_id] = sht
                print(
                    f"Found SHT45: ID {sensor_id} "
                    f"(mux 0x{address:02X}, channel {channel})",
                    flush=True,
                )
                break
            except (ValueError, OSError, RuntimeError) as error:
                release_bus()
                last_error = f"{type(error).__name__}: {error}"
                time.sleep(0.1)
        else:
            print(
                f"ID {sensor_id} (mux 0x{address:02X}, channel {channel}): "
                f"no sensor ({last_error})",
                flush=True,
            )

if not sensors:
    raise RuntimeError("No SHT45 sensors found on any mux channel")

print(
    f"Found SHT45: {sensor_name(sensor_id)} "
    f"(ID {sensor_id}, mux 0x{address:02X}, channel {channel})",
    flush=True,
)

# ---------------------------------------------------------
# 3. GPIO CONFIGURATION (BCM GPIO NUMBERS)
# ---------------------------------------------------------

# GPIO 13: Fan PWM speed control
# FAN_SPEED: 0.0 = off, 1.0 = 100% duty cycle
FAN_SPEED = 1.0

fan = PWMOutputDevice(
    pin=13,
    frequency=10000,
    initial_value=FAN_SPEED,
)

# GPIO 27: Cooling system ON/OFF
# Starts OFF. Change to True if cooling should start ON.
COOLING_ON_AT_START = False

cooling = OutputDevice(
    pin=27,
    active_high=True,
    initial_value=COOLING_ON_AT_START,
)

# GPIO 18: Heatpad PWM
# PID controls duty cycle between 0% and 100%.
heatpad = PWMOutputDevice(
    pin=18,
    frequency=1000,
    initial_value=0,
)

# GPIO 22: Humidity actuator ON/OFF
# Turns ON when the program starts.
humidity_actuator = OutputDevice(
    pin=22,
    active_high=True,
    initial_value=True,
)

print("Fan: ON")
print(f"Cooling system: {'ON' if cooling.value else 'OFF'}")
print("Humidity actuator: ON")

# ---------------------------------------------------------
# 4. PID CONFIGURATION
# ---------------------------------------------------------
TARGET_TEMP_C = 37.5

# Mux channels averaged for PID control. Any other channel is logged
# but does not affect the heatpad (channel 5 is the control sensor).
AVERAGE_CHANNELS = (0, 1, 2, 3, 4)

pid = PID(
    Kp=5.0,
    Ki=0.05,
    Kd=1.0,
    setpoint=TARGET_TEMP_C,
    output_limits=(0.0, 1.0),
)

pid.sample_time = 1.0

print(f"Controller active. Target: {TARGET_TEMP_C} °C")

# ---------------------------------------------------------
# 5. CONTROL LOOP
# ---------------------------------------------------------
try:
    while True:
        readings = {}

        # Read temperature and humidity from each sensor.
        for channel, sht in sensors.items():
            try:
                temperature_c, humidity_rh = sht.measurements
                readings[channel] = (
                    temperature_c,
                    humidity_rh,
                )
            except (OSError, RuntimeError) as error:
                release_bus()
                print(f"{sensor_name(channel)}: read failed: {error}")

        average_temps = [
            readings[channel][0]
            for channel in AVERAGE_CHANNELS
            if channel in readings
        ]

        # Fail safe: disable heating if no averaged sensor could be read.
        if not average_temps:
            heatpad.value = 0
            print("WARNING: No valid readings to average; heatpad OFF")
            time.sleep(1)
            continue

        if len(readings) < len(sensors):
            print(
                f"WARNING: only {len(readings)} of {len(sensors)} "
                f"sensors read this cycle"
            )

        # Average temperature for PID control.
        temp_c = sum(average_temps) / len(average_temps)

        # Calculate heatpad PWM duty cycle.
        p_duty_cycle = pid(temp_c)
        heatpad.value = p_duty_cycle

        # Keep fan and humidity actuator ON.
        fan.on()
        humidity_actuator.on()

        # Cooling is independently controlled.
        # To turn cooling ON:  cooling.on()
        # To turn cooling OFF: cooling.off()

        # Append readings to this run's CSV file.
        timestamp = datetime.now().isoformat(timespec="seconds")

        with open(CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)

            for channel, (ch_temp, ch_humidity) in readings.items():
                writer.writerow([
                    timestamp,
                    channel,
                    sensor_name(channel),
                    round(ch_temp, 2),
                    round(ch_temp * 9 / 5 + 32, 2),
                    round(ch_humidity, 1),
                    round(temp_c, 2),
                    round(p_duty_cycle * 100, 1),
                    round(fan.value * 100, 1),
                ])

            f.flush()

        # Print diagnostic information.
        for channel, (ch_temp, ch_humidity) in readings.items():
            print(
                f"{sensor_name(channel):<14}"
                f"Temp: {ch_temp:.2f} °C | "
                f"Humidity: {ch_humidity:.1f}% RH"
            )

        print(
            f"Average: {temp_c:.2f} °C | "
            f"Target: {TARGET_TEMP_C:.1f} °C | "
            f"Heatpad: {p_duty_cycle * 100:.1f}% | "
            f"Fan: {'ON' if fan.value else 'OFF'} | "
            f"Cooling: {'ON' if cooling.value else 'OFF'} | "
            f"Humidity actuator: "
            f"{'ON' if humidity_actuator.value else 'OFF'}"
        )

        time.sleep(1)

except KeyboardInterrupt:
    print("\nStopping controller...")

finally:
    # Turn all outputs OFF on shutdown.
    for device in (
        heatpad,
        fan,
        cooling,
        humidity_actuator,
    ):
        device.off()
        device.close()

    print("All actuator outputs turned OFF.")
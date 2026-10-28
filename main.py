
import time
import csv
from pathlib import Path
from datetime import datetime

import board
import adafruit_sht4x
import adafruit_tca9548a
from gpiozero import PWMOutputDevice, OutputDevice
from simple_pid import PID

# ---------------------------------------------------------
# 1. CSV DATA LOGGING — NEW FILE FOR EACH RUN
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
# 2. SENSOR INITIALIZATION
# ---------------------------------------------------------

# ---------------------------------------------------------
# 2. SENSOR INITIALIZATION
# ---------------------------------------------------------
print("DEBUG 1: Starting I2C initialization", flush=True)
i2c = board.I2C()

print("DEBUG 2: Creating multiplexer", flush=True)
mux = adafruit_tca9548a.TCA9548A(i2c)

print("DEBUG 3: Multiplexer initialized", flush=True)
print("DEBUG 4: Beginning sensor discovery", flush=True)

sensors = {}

for channel in range(8):
    print(f"DEBUG 5: Checking channel {channel}", flush=True)

    try:
        channel_i2c = mux[channel]
        print(f"DEBUG 6: Channel {channel} selected", flush=True)

        sht = adafruit_sht4x.SHT4x(channel_i2c)
        print(f"DEBUG 7: Sensor detected on channel {channel}",
              flush=True)

        sht.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION
        sensors[channel] = sht

        print(f"Found SHT45 on mux channel {channel}", flush=True)

    except (ValueError, OSError, RuntimeError) as error:
        print(
            f"Channel {channel} unavailable: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )

if not sensors:
    raise RuntimeError("No SHT45 sensors found on any mux channel")

print(f"DEBUG 8: Found {len(sensors)} sensor(s)", flush=True)


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
                print(f"Channel {channel}: read failed: {error}")

        # Fail safe: disable heating if all sensor reads fail.
        if not readings:
            heatpad.value = 0
            print("WARNING: No valid sensor readings; heatpad OFF")
            time.sleep(1)
            continue

        # Average temperature for PID control.
        temp_c = sum(
            temperature for temperature, humidity in readings.values()
        ) / len(readings)

        # Calculate heatpad PWM duty cycle.
        p_duty_cycle = pid(temp_c)
        heatpad.value = p_duty_cycle

        # Keep fan and humidity actuator ON.
        fan.on()
        humidity_actuator.on()

        # Cooling is independently controlled.
        # To turn cooling ON:  cooling.on()
        # To turn cooling OFF: cooling.off()

        # Append readings to the daily CSV file.
        timestamp = datetime.now().isoformat(timespec="seconds")

        with open(CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)

            for channel, (ch_temp, ch_humidity) in readings.items():
                writer.writerow([
                    timestamp,
                    channel,
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
                f"Sensor {channel}: "
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

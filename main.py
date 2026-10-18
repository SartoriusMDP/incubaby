import time
import board
import adafruit_sht4x
import adafruit_tca9548a
from gpiozero import PWMOutputDevice
from simple_pid import PID

import csv
import os
from datetime import datetime

# ---------------------------------------------------------
# CSV Data Logging
# ---------------------------------------------------------
DATA_DIR = "sensor_data"
os.makedirs(DATA_DIR, exist_ok=True)

TODAY = datetime.now().strftime("%Y-%m-%d")
CSV_FILE = os.path.join(DATA_DIR, f"{TODAY}.csv")

# Create the CSV header only if the file doesn't exist yet
if not os.path.exists(CSV_FILE):
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
            "Fan_Duty_Percent"
        ])

print(f"Logging sensor data to: {CSV_FILE}")

# ---------------------------------------------------------
# 1. Hardware Initialization
# ---------------------------------------------------------
# Sensor Setup: SparkFun Qwiic Mux (TCA9548A, default address 0x70)
i2c = board.I2C()
mux = adafruit_tca9548a.TCA9548A(i2c)

# Find an SHT45 on each mux channel (0-7); skip empty channels
sensors = {}
for channel in range(8):
    try:
        sht = adafruit_sht4x.SHT4x(mux[channel])
        sht.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION
        sensors[channel] = sht
        print(f"Found SHT45 on mux channel {channel}")
    except (ValueError, OSError, RuntimeError):
        pass

if not sensors:
    raise RuntimeError("No SHT45 sensors found on any mux channel")

# MOSFET Output Pin (GPIO 18 / Pin 12 physically)
# frequency=1000 Hz provides smooth DC PWM switching
heatpad = PWMOutputDevice(pin=18, frequency=1000)

# Fan PWM Pin (GPIO 13 / Pin 33 physically)
# 10 kHz is the highest frequency lgpio supports; 4-pin PC fans expect ~25 kHz
# but generally accept this. FAN_SPEED is 0.0 (off) to 1.0 (full speed).
# Testing: fan runs at FAN_SPEED when avg temp >= FAN_ON_TEMP_C, else off.
FAN_SPEED = 1.0
FAN_ON_TEMP_C = 26.0
fan = PWMOutputDevice(pin=13, frequency=10000)

# ---------------------------------------------------------
# 2. PID Configuration
# ---------------------------------------------------------
TARGET_TEMP_C = 37.5  # Target setpoint in °C

pid = PID(
    Kp=5.0,    # Proportional: aggressive push when far from target
    Ki=0.05,   # Integral: eliminates steady-state error over time
    Kd=1.0,    # Derivative: dampens overshoot as target approaches
    setpoint=TARGET_TEMP_C,
    output_limits=(0.0, 1.0)  # Restricts PWM to 0% (off) - 100% (full on)
)

# Optional: Set sample time matching your sleep loop (1 second)
pid.sample_time = 1.0 

print(f"Controller active. Target setpoint: {TARGET_TEMP_C} °C")

# ---------------------------------------------------------
# 3. Control Loop
# ---------------------------------------------------------
try:
    while True:
        # Step A: Read temperature from every SHT45 on the mux
        readings = {}
        for channel, sht in sensors.items():
            try:
                readings[channel] = sht.measurements
            except (OSError, RuntimeError):
                print(f"  Channel {channel}: read failed")

        if not readings:
            heatpad.value = 0  # No valid readings: fail safe with heat off
            time.sleep(1)
            continue

        # Control on the average temperature across all sensors
        temp_c = sum(t for t, _ in readings.values()) / len(readings)

        # Step B: Calculate required PID output (0.0 to 1.0)
        p_duty_cycle = pid(temp_c)
        
        # Step C: Update MOSFET PWM duty cycle
        heatpad.value = p_duty_cycle

        # Step C2: Fan on at or above FAN_ON_TEMP_C, off below it
        # fan.value = FAN_SPEED if temp_c >= FAN_ON_TEMP_C else 0
        fan.value = FAN_SPEED

        # Step C3: Save sensor readings to CSV
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
                    round(fan.value * 100, 1)
                ])

            f.flush()

        
        # Step D: Log operational metrics
        for channel, (ch_temp, ch_humidity) in readings.items():
            print(
                f"  Channel {channel}: "
                f"Temp: {ch_temp:.2f}°C | "
                f"Humidity: {ch_humidity:.1f}%"
            )
        print(
            f"Avg Temp: {temp_c:.2f}°C | "
            f"Target: {TARGET_TEMP_C:.1f}°C | "
            f"Heat Power: {p_duty_cycle * 100:5.1f}% | "
            f"Fan: {fan.value * 100:5.1f}%"
        )
        
        time.sleep(1)

except KeyboardInterrupt:
    print("\nShutting down controller...")
finally:
    heatpad.value = 0  # Turn off heatpad safely
    heatpad.close()
    fan.value = 0
    fan.close()
import time
import board
import adafruit_sht4x
from gpiozero import PWMOutputDevice
from simple_pid import PID

# ---------------------------------------------------------
# 1. Hardware Initialization
# ---------------------------------------------------------
# Sensor Setup
i2c = board.I2C()
sht = adafruit_sht4x.SHT4x(i2c)
sht.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION

# MOSFET Output Pin (GPIO 18 / Pin 12 physically)
# frequency=1000 Hz provides smooth DC PWM switching
heatpad = PWMOutputDevice(pin=18, frequency=1000)

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
        # Step A: Read temperature from SHT45
        temp_c, humidity = sht.measurements
        
        # Step B: Calculate required PID output (0.0 to 1.0)
        p_duty_cycle = pid(temp_c)
        
        # Step C: Update MOSFET PWM duty cycle
        heatpad.value = p_duty_cycle
        
        # Step D: Log operational metrics
        print(
            f"Temp: {temp_c:.2f}°C | "
            f"Target: {TARGET_TEMP_C:.1f}°C | "
            f"Heat Power: {p_duty_cycle * 100:5.1f}% | "
            f"Humidity: {humidity:.1f}%"
        )
        
        time.sleep(1)

except KeyboardInterrupt:
    print("\nShutting down controller...")
finally:
    heatpad.value = 0  # Turn off heatpad safely
    heatpad.close()
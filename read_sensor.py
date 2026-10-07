import time
import board
import adafruit_sht4x

i2c = board.I2C()
sht = adafruit_sht4x.SHT4x(i2c)
sht.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION

print("Reading SHT45... press Ctrl+C to stop")

while True:
    temp_c, humidity = sht.measurements
    temp_f = temp_c * 9 / 5 + 32
    print(f"{temp_c:.2f} °C | {temp_f:.2f} °F | {humidity:.1f} % RH")
    time.sleep(1)


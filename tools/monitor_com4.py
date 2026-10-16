import serial
import time
import sys

port = 'COM4'
baud = 115200

print(f"Opening {port} with DTR=False, RTS=False...")
try:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.5
    ser.dtr = False
    ser.rts = False
    ser.open()
    print(f"Connected to {port}. Streaming lines to stdout...")
    sys.stdout.flush()

    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore')
            if line:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] {line.rstrip()}")
                sys.stdout.flush()
        except serial.SerialException as e:
            print(f"Serial error: {e}")
            break
        except Exception as e:
            time.sleep(0.1)
except Exception as e:
    print(f"Could not open {port}: {e}")


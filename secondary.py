#!/usr/bin/env python3

import selectors
import socket
import sys
import types
import pickle
import json

sel = selectors.DefaultSelector()

sensor_data = {
    "Temperature": 1,
    "Humidity": 2,
    "Soil Moisture": 3,
    "Wind Speed": 4
}


def accept_wrapper(sock):
    conn, addr = sock.accept()  # Should be ready to read
    print(f"Accepted connection from {addr}")
    conn.setblocking(False)
    data = types.SimpleNamespace(
        addr=addr, 
        inb=b"", 
        outb=b""
    )
    events = selectors.EVENT_READ | selectors.EVENT_WRITE
    sel.register(conn, events, data=data)

def service_connection(key, mask):
    sock = key.fileobj
    data = key.data
    if mask & selectors.EVENT_READ:
        recv_data = sock.recv(1024)  # Should be ready to read
        if recv_data:
            data.inb += recv_data
        else:
            print(f"Closing connection to {data.addr}")
            sel.unregister(sock)
            sock.close()
    
    if mask & selectors.EVENT_WRITE: # If "Requesting data." was received, send sensor data.
        if (data.inb and (len(data.inb) >= 16)):
            if (data.inb.decode() == "Requesting data."):
                msg = json.dumps(sensor_data).encode()
                msg_size = f"{len(msg):0>4}".encode()
                data.outb = msg_size + msg
            data.inb = b""
        if (data.outb):
            print(f"Data being sent: {data.outb}")
            num_bytes_sent = sock.send(data.outb)  # Should be ready to write
            data.outb = data.outb[num_bytes_sent:]

# from simpleio import map_range
# import board # Pi4
# import busio # Pi4 GPIO
# import adafruit_ads1x15.ads1015 as ADS          # ADC
# from adafruit_ads1x15.analog_in import AnalogIn # ADC
# from adafruit_seesaw.seesaw import Seesaw # Soil sensor
# import adafruit_sht31d                    # sht30 temperature sensor

# i2c = busio.I2C(board.SCL, board.SDA)

# adc1015 = AnalogIn(ADS.ADS1015(i2c), ADS.P0)
# sht30 = adafruit_sht31d.SHT31D(i2c)
# soilsensor = Seesaw(i2c, addr=0x36)

# def adc_to_wind_speed(V):
#     PI4_VOLTAGE = 3.3
#     ANEMOMETER_MIN_VOLT = 0.4
#     ANEMOMETER_MAX_VOLT = 2
#     MIN_WIND_SPEED = 0
#     MAX_WIND_SPEED = 32.4      

#     MAP_INPUT_ZERO_VALUE = 7679.4
#     ANEMOMETER_RESTING_VALUE = 3264
#     VALUE_OFFSET = MAP_INPUT_ZERO_VALUE - ANEMOMETER_RESTING_VALUE

#     return map_range(((V + VALUE_OFFSET) / 65535) * PI4_VOLTAGE, 
#                      ANEMOMETER_MIN_VOLT, ANEMOMETER_MAX_VOLT,
#                      MIN_WIND_SPEED, MAX_WIND_SPEED
#     )

# my_sensor_datum = {
#     "Temperature": sht30.temperature,
#     "Humidity": sht30.relative_humidity,
#     "Soil Moisture": soilsensor.moisture_read(),
#     "Wind Speed": adc_to_wind_speed(adc1015.value)
# }

if len(sys.argv) != 3:
    print(f"Usage: {sys.argv[0]} <host> <port>")
    sys.exit(1)

host, port = sys.argv[1], int(sys.argv[2])
lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.bind((host, port))
lsock.listen()
print(f"Listening on {(host, port)}")
lsock.setblocking(False)
sel.register(lsock, selectors.EVENT_READ, data=None)

try:
    while True:
        events = sel.select(timeout=None)
        for key, mask in events:
            if key.data is None:
                accept_wrapper(key.fileobj)
            else:
                service_connection(key, mask)
except KeyboardInterrupt:
    print("Caught keyboard interrupt, exiting")
finally:
    sel.close()
#!/usr/bin/env python3
#Primary. AKA the client.

import selectors
import socket
import sys
import types
import time

import board # Pi4
import busio # Pi4 GPIO
import adafruit_ads1x15.ads1015 as ADS          # ADC
from adafruit_ads1x15.analog_in import AnalogIn # ADC
from adafruit_seesaw.seesaw import Seesaw # Soil sensor
import adafruit_sht31d                    # sht30 temperature sensor

from simpleio import map_range

def adc_to_wind_speed(V):
    PI4_VOLTAGE = 3.3
    ANEMOMETER_MIN_VOLT = 0.4
    ANEMOMETER_MAX_VOLT = 2
    MIN_WIND_SPEED = 0
    MAX_WIND_SPEED = 32.4      

    MAP_INPUT_ZERO_VALUE = 7679.4
    ANEMOMETER_RESTING_VALUE = 3264
    VALUE_OFFSET = MAP_INPUT_ZERO_VALUE - ANEMOMETER_RESTING_VALUE

    return map_range(((V + VALUE_OFFSET) / 65535) * PI4_VOLTAGE, 
                     ANEMOMETER_MIN_VOLT, ANEMOMETER_MAX_VOLT,
                     MIN_WIND_SPEED, MAX_WIND_SPEED
    )


# i2c = busio.I2C(board.SCL, board.SDA)

# adc1015 = AnalogIn(ADS.ADS1015(i2c), ADS.P0)
# sht30 = adafruit_sht31d.SHT31D(i2c)
# soilsensor = Seesaw(i2c, addr=0x36)


def start_connections(host, port):
    server_addr = (host, port)
    print(f"Starting connection to {server_addr}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    sock.connect_ex(server_addr)
    return sock


def service_connection(key, mask):
    sock = key.fileobj
    data = key.data
    # if mask & selectors.EVENT_READ:
    #     recv_data = sock.recv(1024)  # Should be ready to read
    #     if recv_data:
    #         print(f"Received {recv_data!r} from connection {data.connid}")
    #         data.recv_total += len(recv_data)
    #     if not recv_data or data.recv_total == data.msg_total:
    #         print(f"Closing connection {data.connid}")
    #         sel.unregister(sock)
    #         sock.close()
    # if mask & selectors.EVENT_WRITE:
    #     if not data.outb and data.messages:
    #         data.outb = data.messages.pop(0)
    #     if data.outb:
    #         print(f"Sending {data.outb!r} to connection {data.connid}")
    #         sent = sock.send(data.outb)  # Should be ready to write
    #         data.outb = data.outb[sent:]


NUM_ARGS = 5
if len(sys.argv) != NUM_ARGS:
    print(f"Usage: {sys.argv[0]} <host1a> <port1> <host2> <port2>")
    sys.exit(1)

host1, port1, host2, port2 = sys.argv[1:NUM_ARGS]
sockets = []
sockets.append(start_connections(host1, int(port1)))
sockets.append(start_connections(host2, int(port2)))

temperature_data = dict()
humidity_data = dict()
soil_moisture_data = dict()
wind_sensor = dict()

try:
    while True:
        for i in range(2):
            sockets[i].send(b"Requesting data.")
            data = sockets[i].recv(1048)
            if not data:
                pass
            print(data)
            time.sleep(1)
        
        time.sleep(1)
            
except KeyboardInterrupt:
    print("Caught keyboard interrupt, exiting")
finally:
    for i in range(2):
        sockets[i].close()
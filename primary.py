#!/usr/bin/env python3
#Primary. AKA the client.


import socket
import sys
import time
import json
import numpy as np
import matplotlib.pyplot as plt
import os
from collections import Counter

SOCKET_TIMEOUT = 2

def dict_avg(sequence):
    avg = Counter()
    num_fields = 4
    for i in range(len(sequence)):
        avg += Counter(sequence[i])
    avg = dict(avg)
    for i in range(num_fields):
        avg[list(avg.keys())[i]] = avg[list(avg.keys())[i]]/num_fields
    return avg

def recvAll(sock, expected_num_bytes_recv):
    num_bytes_recv = 0
    total_data_recv = b""
    while (num_bytes_recv < expected_num_bytes_recv):
        curr_data_recv = sock.recv(1024)
        total_data_recv += curr_data_recv
        num_bytes_recv += len(total_data_recv)
    return total_data_recv

def start_connection(host, port):
    server_addr = (host, port)
    print(f"Starting connection to {server_addr}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect_ex(server_addr)
    sock.settimeout(SOCKET_TIMEOUT)
    return sock

def poll_sensor_data(sock):
    try:
        sock.sendall(b"Requesting data.")
        expected_num_bytes_recv = int(sock.recv(4))
        sensor_datum = json.loads(recvAll(sock, expected_num_bytes_recv))
    except TimeoutError:
        sensor_datum = None
    return sensor_datum




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

my_sensor_datum = {
    "Temperature": 1,
    "Humidity": 2,
    "Soil Moisture": 3,
    "Wind Speed": 4
}

if ((len(sys.argv) % 2) != 1):
    print(f"# of given arguments should be a multiple of 2!")
    print(f"Usage: {sys.argv[0]} <host1> <port1> <host2> <port2> ... <hostn> <portn>")
    sys.exit(1)

num_connections = ((len(sys.argv) - 1) // 2)

colors = None
if num_connections == 2:
    colors = ["#FF0000", "#0000FF", "#00FF00", "#000000"]

sockets = [start_connection(sys.argv[2*i+1], int(sys.argv[2*i+2])) for i in range(num_connections)]
plot_names = [f"Sec{i}" for i in range(num_connections)]
plot_names += ["Primary", "Avg"]

# Create folder if it doesn't exist. Otherwise, plt.savefig() raises an error.
plot_folder_name = "plots"
os.makedirs(f"./{plot_folder_name}", exist_ok=True)

try:
    iteration = 0
    while True:
        sensor_data = [poll_sensor_data(sockets[i]) for i in range(num_connections)]
        print(sensor_data)
        sensor_data.append(my_sensor_datum)
        sensor_data.append(dict_avg(sensor_data))

        # Calculate avg. Blah blah
        #plot_sensor_data(sensor_data, round)

        fig, axs = plt.subplots(2, 2)
        fig.suptitle("RAAAAAAAAAAAAAAAGH")
        axs[0, 0].scatter(
            plot_names,
            [sensor_datum["Soil Moisture"] for sensor_datum in sensor_data],
            c=colors
        )
        axs[0, 0].set_ylabel("Moisture")
        axs[0, 0].set_title("Soil Moisture Sensor")

        axs[0, 1].scatter(
            plot_names,
            [sensor_datum["Temperature"] for sensor_datum in sensor_data],
            c=colors
        )
        axs[0, 1].set_ylabel("Temperature (°C)")
        axs[0, 1].set_title("Temperature Sensor")


        axs[1, 0].scatter(
            plot_names,
            [sensor_datum["Wind Speed"] for sensor_datum in sensor_data],
            c=colors
        )
        axs[1, 0].set_ylabel("Speed (m/s)")
        axs[1, 0].set_title("Wind Speed Sensor")


        axs[1, 1].scatter(
            plot_names,
            [sensor_datum["Humidity"] for sensor_datum in sensor_data],
            c=colors
        )
        axs[1, 1].set_ylabel("Humidity (%)")
        axs[1, 1].set_title("Humidity Sensor")
        fig.tight_layout()

        plt.savefig(f"./{plot_folder_name}/polling-plot-{iteration}.png")
        plt.close(fig)
        time.sleep(1)
        iteration += 1
except KeyboardInterrupt:
    print("Caught keyboard interrupt, exiting")

finally:
    for sock in sockets:
        sock.close()
#!/usr/bin/env python3
#Primary. AKA the client.


import socket
import sys
import time
import json
import matplotlib.pyplot as plt
import os
from collections import Counter

SOCKET_TIMEOUT = 2

def dict_avg(sensor_data):
    # Get the # of datums with no Nones.
    # Assumes that, if the first is not None, then the rest probably aren't None. Good enough.

    valid_sensors = []
    for sensor_datum in sensor_data:
        if (sensor_datum["Temperature"] is not None):
            valid_sensors.append(sensor_datum)

    # num_valid_datums = sum(int(sensor_datum["Temperature"] is not None) for sensor_datum in sensor_data) # list(sensor_datum.keys())[0]]

    avg = Counter()
    for sensor_datum in valid_sensors:
        avg += Counter(sensor_datum)
    avg = dict(avg)

    for key in avg.keys():
        avg[key] = avg[key]/len(valid_sensors)
    
    return avg

def recvAll(sock, expected_num_bytes_recv): #recvAtleast
    total_num_bytes_recv = 0
    total_data_recv = b""
    while (total_num_bytes_recv < expected_num_bytes_recv):
        curr_data_recv = sock.recv(expected_num_bytes_recv - total_num_bytes_recv)
        total_data_recv += curr_data_recv
        total_num_bytes_recv += len(curr_data_recv)
    return total_data_recv

def start_connection(host, port):
    server_addr = (host, port)
    print(f"Starting connection to {server_addr}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(server_addr)
    sock.settimeout(SOCKET_TIMEOUT)
    return sock

def poll_sensor_data(sock):
    try:
        sock.sendall(b"Requesting data.")
        expected_num_bytes_recv = int(sock.recv(4))
        sensor_datum = json.loads(recvAll(sock, expected_num_bytes_recv))
    except (TimeoutError, ValueError): # JSONDecodeError is subclass of ValueError
        sensor_datum = {
            "Temperature": None,
            "Humidity": None,
            "Soil Moisture": None,
            "Wind Speed": None
        }
    return sensor_datum



# Create "plots" folder if it doesn't exist. Otherwise, plt.savefig() raises an error.
plot_folder_name = "plots"
os.makedirs(f"./{plot_folder_name}", exist_ok=True)

def plot_and_save_as_png(sensor_data, number, color_list, x_label_list):
    fig, axs = plt.subplots(2, 2)
    axs[1,0].scatter(
        x_label_list,
        [sensor_datum["Soil Moisture"] for sensor_datum in sensor_data],
        c=color_list
    )
    axs[1,0].set_ylabel("Moisture")
    axs[1,0].set_title("Soil Moisture Sensor")

    axs[0,0].scatter(
        x_label_list,
        [sensor_datum["Temperature"] for sensor_datum in sensor_data],
        c=color_list
    )
    axs[0,0].set_ylabel("Temperature (°C)")
    axs[0,0].set_title("Temperature Sensor")

    axs[1,1].scatter(
        x_label_list,
        [sensor_datum["Wind Speed"] for sensor_datum in sensor_data],
        c=color_list
    )
    axs[1,1].set_ylabel("Wind Speed (m/s)")
    axs[1,1].set_title("Wind Sensor")

    axs[0,1].scatter(
        x_label_list,
        [sensor_datum["Humidity"] for sensor_datum in sensor_data],
        c=color_list
    )
    axs[0,1].set_ylabel("Humidity (%)")
    axs[0,1].set_title("Humidity Sensor")
    fig.tight_layout()

    plt.savefig(f"./{plot_folder_name}/polling-plot-{number}.png")
    plt.close(fig)


from simpleio import map_range
import board # Pi4
import busio # Pi4 GPIO
import adafruit_ads1x15.ads1015 as ADS          # ADC
from adafruit_ads1x15.analog_in import AnalogIn # ADC
from adafruit_seesaw.seesaw import Seesaw # Soil sensor
import adafruit_sht31d                    # sht30 temperature sensor

i2c = busio.I2C(board.SCL, board.SDA)

adc1015 = AnalogIn(ADS.ADS1015(i2c), ADS.P0)
sht30 = adafruit_sht31d.SHT31D(i2c)
soilsensor = Seesaw(i2c, addr=0x36)

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

def my_sensor_datum():
    return {
        "Temperature": sht30.temperature,
        "Humidity": sht30.relative_humidity,
        "Soil Moisture": soilsensor.moisture_read(),
        "Wind Speed": adc_to_wind_speed(adc1015.value)
    }
    # return {
    #     "Temperature": 1,
    #     "Humidity": 2,
    #     "Soil Moisture": 3,
    #     "Wind Speed": 4
    # }

if ((len(sys.argv) % 2) != 1):
    print(f"# of given arguments should be a multiple of 2!")
    print(f"Usage: {sys.argv[0]} <ipaddr1> <port1> <ipaddr2> <port2> ... <ipaddrn> <portn>")
    sys.exit(1)

num_connections = ((len(sys.argv) - 1) // 2)

# sockets = [start_connection(sys.argv[2*i+1], int(sys.argv[2*i+2])) for i in range(num_connections)]

sockets = []
for i in range(num_connections):
    try:
        sockets.append(start_connection(sys.argv[2*i+1], int(sys.argv[2*i+2])))
    except ConnectionRefusedError:
        pass

try:
    iteration = 0
    while True:
        # sensor_data = [poll_sensor_data(sockets[i]) for i in range(num_connections)]
        sockets_copy = sockets
        sensor_data = list()
        for sock in sockets_copy:
            try:
                sensor_data.append(poll_sensor_data(sock))
            except BrokenPipeError:
                sock.close()
                sockets.remove(sock)
        sensor_data.append(my_sensor_datum())
        sensor_data.append(dict_avg(sensor_data))

        color_list = ["#FF0000", "#0000FF", "#00FF00", "#000000"] if (len(sockets) == 2) else None # Red, Blue, Green, Black
        x_label_list = [f"Sec{i}" for i in range(len(sockets))] + ["Primary", "Avg"]
        print(f"Round {iteration} ######################")
        for i in range(len(sensor_data)):
            print(f">{x_label_list[i]:>8}:\t{sensor_data[i]}".expandtabs(11))
        
        plot_and_save_as_png(sensor_data, iteration, color_list, x_label_list)

        iteration += 1
        time.sleep(1)
except KeyboardInterrupt:
    print("Caught keyboard interrupt, exiting")
except Exception as e:
    print(e)
finally:
    for sock in sockets:
        sock.close()
import socket
import selectors
import types
import logging
import argparse
import time
import json
import os 
import matplotlib
import matplotlib.pyplot as plt
import board
import busio
from simpleio import map_range
from adafruit_seesaw.seesaw import Seesaw
import adafruit_sht31d
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import numpy as np

matplotlib.use('Agg') 

# im not sure if we are manually assigning the ips or using isc-dhcp but if we manually assign it things would probably be easier and we can put the ips here
PI_CONFIG = {
    1: {"ip": "169.233.1.1"}, 
    2: {"ip": "169.233.1.2"},
    3: {"ip": "169.233.1.3"}, 
}

LISTEN_PORT = 65432
DEFAULT_PI_IP = "127.0.0.1" 

base_slogger = logging.getLogger("(RingNode)")
base_slogger.setLevel(level=logging.INFO)
ch = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - PI_ID:%(pi_id)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
base_slogger.addHandler(ch)
base_slogger.propagate = False

class RingNode:
    def __init__(self, host_ip, port, pi_id, ring_config, logger):
        self.logger = logger
        self.logger.debug("Initializing RingNode...")

        self.sel = selectors.DefaultSelector()
        self.host_ip = host_ip
        self.port = port
        self.pi_id = pi_id
        self.ring_config = ring_config

        self.next_pi_id = (self.pi_id % 3) + 1
        self.next_pi_ip = self.ring_config.get(self.next_pi_id, {}).get("ip", DEFAULT_PI_IP)
        self.next_pi_port = LISTEN_PORT

        self.server_socket = None
        self.current_round = 0 

        self.i2c = None
        self.sensor_sht31d = None
        self.sensor_seesaw = None
        self.sensor_ads1015 = None
        self.sensor_ads_chan = None
        self._init_sensors() 

        self.logger.info(f"Node initialized. Will send to Pi{self.next_pi_id} at {self.next_pi_ip}:{self.next_pi_port}")

    def _init_sensors(self):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor_sht31d = adafruit_sht31d.SHT31D(self.i2c)
        self.sensor_seesaw = Seesaw(self.i2c, addr=0x36)
        self.sensor_ads1015 = ADS.ADS1015(self.i2c)
        self.sensor_ads_chan = AnalogIn(self.sensor_ads1015, ADS.P0)

    def _get_local_sensor_data(self):
        data_points = []

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) 

        temp = self.sensor_sht31d.temperature
        humidity = self.sensor_sht31d.relative_humidity
        data_points.append({"pi_id": self.pi_id, "sensor": "temperature_sht31d", "value": round(temp, 2), "unit": "C", "timestamp": timestamp})
        data_points.append({"pi_id": self.pi_id, "sensor": "humidity_sht31d", "value": round(humidity, 2), "unit": "%", "timestamp": timestamp})

        soil_moisture = self.sensor_seesaw.moisture_read()
        soil_temp = self.sensor_seesaw.get_temp()
        data_points.append({"pi_id": self.pi_id, "sensor": "soil_moisture_seesaw", "value": soil_moisture, "unit": "raw", "timestamp": timestamp})
        data_points.append({"pi_id": self.pi_id, "sensor": "soil_temperature_seesaw", "value": round(soil_temp,2), "unit": "C", "timestamp": timestamp})

        voltage = self.sensor_ads_chan.voltage
        speed = map_range(voltage, 0.4, 2.0, 0.0, 32.4)
        data_points.append({"pi_id": self.pi_id, "sensor": "wind_speed_ads1015", "value": round(speed,2), "unit": "m/s", "timestamp": timestamp})
        
        self.logger.info(f"Collected {len(data_points)} local data points on Pi {self.pi_id}.")
        return data_points

    def _client_send_to_next(self, payload_dict):
        message_payload_json = json.dumps(payload_dict)
        self.logger.info(f"Attempting to send to Pi{self.next_pi_id} ({self.next_pi_ip}): {message_payload_json[:150]}...")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock_client:
                sock_client.settimeout(10.0) 
                sock_client.connect((self.next_pi_ip, self.next_pi_port))
                sock_client.sendall(message_payload_json.encode('utf-8'))
            self.logger.info(f"Successfully sent data to Pi{self.next_pi_id}.")
        except socket.timeout:
            self.logger.error(f"Timeout connecting to Pi{self.next_pi_id} at {self.next_pi_ip}.")
        except ConnectionRefusedError:
            self.logger.error(f"Connection refused by Pi{self.next_pi_id} at {self.next_pi_ip}.")
        except Exception as e:
            self.logger.error(f"Error sending data to Pi{self.next_pi_id}: {e}")

    def _plot_data_and_prepare_response(self, received_payload):
        round_num = received_payload.get("round", "unknown_round")
        all_data_points = received_payload.get("data_points", [])

        self.logger.info(f"Pi3: Plotting data for round {round_num}. Received {len(all_data_points)} total data points.")

        sensors_to_plot = {
            "Temperature": "temperature_sht31d",
            "Humidity": "humidity_sht31d",
            "Soil Moisture": "soil_moisture_seesaw",
            "Wind Speed": "wind_speed_ads1015"
        }
        
        pi_sensor_data = {1: {}, 2: {}, 3: {}}
        for dp in all_data_points:
            pi_id = dp.get("pi_id")
            sensor_key = dp.get("sensor")
            value = dp.get("value")
            unit = dp.get("unit", "")
            if pi_id in pi_sensor_data and sensor_key:
                pi_sensor_data[pi_id][sensor_key] = {"value": value, "unit": unit}


        # Create a single figure with 2x2 subplots
        fig, axs = plt.subplots(2, 2, figsize=(17, 12))
        axs = axs.flatten()

        fig.suptitle(f"Sensor Data Overview - Round {round_num}", fontsize=16)

        x_categories = ["Pi 1", "Pi 2", "Pi 3", "Average"]

        for idx, (plot_title, sensor_key) in enumerate(sensors_to_plot.items()):
            ax = axs[idx]
            self.logger.debug(f"Processing subplot for {plot_title} (sensor key: {sensor_key})")
            
            pi_values_for_plot = []
            raw_pi_values_for_avg = []
            current_unit = ""

            for pi_num in [1, 2, 3]:
                data = pi_sensor_data[pi_num].get(sensor_key)
                if data and isinstance(data["value"], (int, float)): 
                    value = data["value"]
                    pi_values_for_plot.append(value)
                    raw_pi_values_for_avg.append(value)
                    if not current_unit: current_unit = data["unit"]
                else:
                    pi_values_for_plot.append(np.nan)
                    raw_pi_values_for_avg.append(np.nan)
                    if data and not current_unit: 
                        current_unit = data["unit"]
            
            average_value = np.nan
            if any(not np.isnan(v) for v in raw_pi_values_for_avg):
                average_value = np.nanmean([v for v in raw_pi_values_for_avg if not np.isnan(v)])
            
            y_values_to_plot = pi_values_for_plot + [average_value]

            ax.plot(x_categories, y_values_to_plot, marker='o', linestyle='None', markersize=10, mfc='red', mec='black')

            for i, y_val in enumerate(y_values_to_plot):
                if not np.isnan(y_val):
                    ax.text(x_categories[i], y_val, f'{y_val:.2f}', ha='center', va='bottom', fontsize=9)

            ax.set_title(plot_title, fontsize=12)
            y_axis_label = f"{plot_title}"
            if current_unit:
                y_axis_label += f" ({current_unit})"
            ax.set_ylabel(y_axis_label, fontsize=10)
            ax.grid(True, linestyle='--', alpha=0.7)
            ax.tick_params(axis='x', labelsize=9)
            ax.tick_params(axis='y', labelsize=9)

            # Set y-limits to provide some padding around min/max points, handles all NaN case
            valid_ys = [y for y in y_values_to_plot if not np.isnan(y)]
            if valid_ys:
                min_y, max_y = min(valid_ys), max(valid_ys)
                padding = (max_y - min_y) * 0.15 if (max_y - min_y) > 0 else 1.0 # Ensure some padding
                ax.set_ylim(min_y - padding, max_y + padding)
            else: # All NaN case
                 ax.set_ylim(0, 1) # Default Y range if no data


        plt.tight_layout(rect=[0, 0, 1, 0.95])

        plot_filename = f"token-plot-{round_num}.png"
        plt.savefig(plot_filename)
        self.logger.info(f"Saved combined plot: {plot_filename}")
    
        plt.close(fig)

        response_for_pi1 = {
            "round": round_num,
            "status": "cycle_complete_plots_generated",
            "message": f"Pi3 processed data and generated combined plot '{plot_filename}' for round {round_num}."
        }
        return response_for_pi1

    def _handle_ring_message(self, received_json_str):
        try:
            received_payload = json.loads(received_json_str)
            self.logger.debug(f"Processing received payload: {received_payload}")
        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode JSON: {received_json_str}")
            return

        current_round_from_payload = received_payload.get("round", self.current_round)
        aggregated_data_points = received_payload.get("data_points", []) # Start with what was received
        
        if self.pi_id == 1 and received_payload.get("status"): 
            self.logger.info(f"Pi1: Cycle for round {current_round_from_payload} complete.")
            
            self.current_round = current_round_from_payload + 1 
            self.logger.info(f"Pi1: Starting new cycle for round {self.current_round}. Collecting sensor data...")
            time.sleep(5) 
            
            local_data_pi1 = self._get_local_sensor_data()
            new_payload_to_send = {"round": self.current_round, "data_points": local_data_pi1}
            self._client_send_to_next(new_payload_to_send)

        elif self.pi_id == 2: # Pi2 receives from Pi1
            self.logger.info(f"Pi2: Received data from Pi1 for round {current_round_from_payload}. Aggregating...")
            local_data_pi2 = self._get_local_sensor_data() 
            aggregated_data_points.extend(local_data_pi2) # Add Pi2's data
            
            payload_to_send_to_pi3 = {"round": current_round_from_payload, "data_points": aggregated_data_points}
            self._client_send_to_next(payload_to_send_to_pi3)

        elif self.pi_id == 3: # Pi3 receives from Pi2
            self.logger.info(f"Pi3: Received data from Pi1/Pi2 for round {current_round_from_payload}. Aggregating...")
            local_data_pi3 = self._get_local_sensor_data() 
            aggregated_data_points.extend(local_data_pi3) 
            
            payload_for_plotting = {"round": current_round_from_payload, "data_points": aggregated_data_points}
            response_for_pi1 = self._plot_data_and_prepare_response(payload_for_plotting)
            self._client_send_to_next(response_for_pi1)


    def _initiate_first_round_for_pi1(self):
        if self.pi_id == 1:
            self.current_round = 1 
            self.logger.info(f"Pi1: Initiating first round ({self.current_round}). Collecting sensor data...")
            time.sleep(7) 
            
            local_data_pi1 = self._get_local_sensor_data() # Pi1 collects its initial data
            initial_payload = {"round": self.current_round, "data_points": local_data_pi1}
            self._client_send_to_next(initial_payload)

    def accept_wrapper(self, sock):
        conn, addr = sock.accept()
        self.logger.info(f"Accepted connection from {addr}")
        conn.setblocking(False)
        data = types.SimpleNamespace(addr=addr, inb=b"")
        self.sel.register(conn, selectors.EVENT_READ, data=data)

    def service_connection(self, key: selectors.SelectorKey, mask):
        sock = key.fileobj
        data = key.data
        if mask & selectors.EVENT_READ:
            received_bytes_total = b""
            try:
                while True: 
                    chunk = sock.recv(4096) 
                    if not chunk: 
                        break
                    received_bytes_total += chunk

                if received_bytes_total:
                    received_json_str = received_bytes_total.decode('utf-8')
                    self.logger.debug(f"Received raw JSON from {data.addr}: {received_json_str[:200]}...")
                    self._handle_ring_message(received_json_str)
                else:
                    self.logger.info(f"Closing connection to {data.addr} (previous Pi closed it, no data).")
            except ConnectionResetError:
                self.logger.warning(f"Connection reset by {data.addr}. Closing connection.")
            except socket.timeout:
                self.logger.warning(f"Socket timeout during recv from {data.addr}. Closing connection.")
            except Exception as e:
                self.logger.error(f"Error during recv from {data.addr}: {e}. Closing connection.")
            finally:
                self.sel.unregister(sock)
                sock.close()

    def run(self):
        self.logger.debug("Starting RingNode server component...")
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.host_ip, self.port))
        except OSError as e:
            self.logger.error(f"Failed to bind to {self.host_ip}:{self.port} - {e}.")
            return 
            
        self.server_socket.listen()
        self.logger.info(f"Server component listening on {self.host_ip}:{self.port}")
        self.server_socket.setblocking(False)
        self.sel.register(self.server_socket, selectors.EVENT_READ, data=None)

        if self.pi_id == 1:
            import threading
            init_thread = threading.Thread(target=self._initiate_first_round_for_pi1, daemon=True)
            init_thread.start()

        try:
            while True:
                events = self.sel.select(timeout=1.0)
                for key, mask in events:
                    if key.data is None:
                        self.accept_wrapper(key.fileobj)
                    else:
                        self.service_connection(key, mask)
        except KeyboardInterrupt:
            self.logger.info("Caught keyboard interrupt, exiting...")
        finally:
            if self.server_socket:
                try:
                    self.sel.unregister(self.server_socket)
                except Exception: pass 
                self.server_socket.close()
            self.sel.close()
            self.logger.info("RingNode server component shut down.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ring Communication Node for Raspberry Pi.")
    parser.add_argument("pi_id", type=int, choices=[1, 2, 3], help="ID of this Pi node (1, 2, or 3)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    pi_id_arg = args.pi_id

    logger_with_context = logging.LoggerAdapter(base_slogger, {'pi_id': pi_id_arg})
    if args.debug:
        base_slogger.setLevel(logging.DEBUG)
        logger_with_context.info("Debug logging enabled.")
    else:
        base_slogger.setLevel(logging.INFO)

    my_ip_address = PI_CONFIG.get(pi_id_arg, {}).get("ip", DEFAULT_PI_IP)
    if my_ip_address == DEFAULT_PI_IP and pi_id_arg in PI_CONFIG:
         logger_with_context.warning(f"IP for Pi {pi_id_arg} not found in PI_CONFIG, using fallback {DEFAULT_PI_IP}. Please update PI_CONFIG.")

    logger_with_context.info(f"Starting Node {pi_id_arg} with IP {my_ip_address} on port {LISTEN_PORT}")

    node = RingNode(host_ip=my_ip_address,
                    port=LISTEN_PORT,
                    pi_id=pi_id_arg,
                    ring_config=PI_CONFIG,
                    logger=logger_with_context)
    node.run()

    logger_with_context.info(f"Node {pi_id_arg} has finished.")
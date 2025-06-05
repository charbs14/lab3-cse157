import socket
import selectors
import subprocess
import threading
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
from mysql.connector import MySQLConnection, Error
import numpy as np
from datetime import datetime

from simpleio import map_range
from adafruit_seesaw.seesaw import Seesaw
import adafruit_sht31d
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn


matplotlib.use('Agg') 

PI_CONFIG = {
    1: {"ip": "169.233.97.1"},
    2: {"ip": "169.233.97.2"},
    3: {"ip": "169.233.97.3"},
}

DB_HOST = '10.0.0.178'
DB_PORT = 3306
DB = 'piSenseDB'
DB_USER = ''
DB_PASSWORD = ''

LISTEN_PORT = 65432
DEFAULT_PI_IP = "127.0.0.1" 
REJOIN_CHECK_INTERVAL = 3
INITIAL_CYCLE_DELAY = 5

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
        self.static_pi_config = ring_config

        self.ring_state_lock = threading.Lock() # Lock for thread-safe state changes
        self.ring_state = {
            pid: {"ip": info["ip"], "status": "active"}
            for pid, info in self.static_pi_config.items()
        }


        self.server_socket = None
        self.current_round = 0 

        self.i2c = None
        self.sensor_sht31d = None
        self.sensor_seesaw = None
        self.sensor_ads1015 = None
        self.sensor_ads_chan = None
        self._init_sensors() 


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

    def _get_active_pi_ids(self):
        with self.ring_state_lock:
            return sorted([pid for pid, info in self.ring_state.items() if info["status"] == "active"])

    def _get_next_active_pi_id(self, current_pi_id):
        active_ids = self._get_active_pi_ids()
        if not active_ids:
            return None
        try:
            current_index = active_ids.index(current_pi_id)
            next_index = (current_index + 1) % len(active_ids)
            return active_ids[next_index]
        except ValueError:
            return active_ids[0]

    def _mark_node_as_inactive(self, pi_id_to_mark):
        with self.ring_state_lock:
            if self.ring_state[pi_id_to_mark]["status"] == "active":
                self.ring_state[pi_id_to_mark]["status"] = "inactive"
                self.logger.warning(f"Marked Pi {pi_id_to_mark} as INACTIVE due to communication failure.")

    def _upload_to_db(self, payload_dict):
        all_data_points = payload_dict.get("data_points", [])
        pi_sensor_data = {1: {}, 2: {}, 3: {}}
        for dp in all_data_points:
            pi_id = dp.get("pi_id")
            sensor_key = dp.get("sensor")
            value = dp.get("value")
            unit = dp.get("unit", "")
            if pi_id in pi_sensor_data and sensor_key:
                pi_sensor_data[pi_id][sensor_key] = value

        for pi_num, data in pi_sensor_data.items():
            com_id = self._insert_data(pi_num, data.get('temperature_sht31d'), data.get('humidity_sht31d'), data.get('soil_moisture_seesaw'), data.get('wind_speed_ads1015'))
            self.logger.info(f"Successfully sent Pi{pi_num} data to sensor_readings{pi_num}. ID: {com_id}")

        
    def _insert_data(self, pi_id, temp, hum, soil, wind):
        query = f'INSERT INTO sensor_readings{pi_id} ' \
                'VALUES(%s, %s, %s, %s, %s)'
        self.logger.info(f'temp: {temp}')
        self.logger.info(f'hum: {hum}')
        self.logger.info(f'soil: {soil}')
        self.logger.info(f'wind: {wind}')
        args = (temp, hum, soil, wind, datetime.now())
        com_id = None
        conn = None
        try:
            conn = MySQLConnection(host=DB_HOST, port=DB_PORT, database=DB, user=DB_USER, password=DB_PASSWORD)
            with conn.cursor() as cursor:
                cursor.execute(query, args)
                com_id = cursor.lastrowid
            conn.commit() 
            return com_id
        except Error as error:
            print(error)

    def _client_send_to_next(self, payload_dict):
        with self.ring_state_lock:
            payload_dict['ring_state'] = self.ring_state

        target_pi_id = self._get_next_active_pi_id(self.pi_id)

        if target_pi_id is None or target_pi_id == self.pi_id:
            self.logger.warning("No other active nodes to send to. Operating in SOLO mode.")
            self._handle_ring_message(json.dumps(payload_dict))
            return

        target_ip = self.static_pi_config[target_pi_id]["ip"]
        message_payload_json = json.dumps(payload_dict)
        self.logger.info(f"Attempting to send to next active node: Pi {target_pi_id} at {target_ip}")
        
        # try to send, with retry and failover logic
        retries = 2
        for i in range(retries):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock_client:
                    sock_client.settimeout(10.0)
                    sock_client.connect((target_ip, self.port))
                    sock_client.sendall(message_payload_json.encode('utf-8'))
                self.logger.info(f"Successfully sent data to Pi {target_pi_id}.")
                return
            except (socket.timeout, ConnectionRefusedError) as e:
                self.logger.error(f"Connection to Pi {target_pi_id} failed: {e}. Retry {i+1}/{retries}...")
                time.sleep(3)
        
        # if all retries fail, mark the node as inactive and try the next one
        self.logger.error(f"All retries to Pi {target_pi_id} failed. Marking as inactive.")
        self._mark_node_as_inactive(target_pi_id)
        
        # recursively call self to try the new next node.
        self._client_send_to_next(payload_dict)


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
        """
        Handles incoming messages with dynamic role logic instead of static ID checks.
        """
        try:
            payload = json.loads(received_json_str)
        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode JSON: {received_json_str}")
            return

        if 'ring_state' in payload and payload['ring_state']:
            payload['ring_state'] = {int(k): v for k, v in payload['ring_state'].items()}
        
        with self.ring_state_lock:
            self.ring_state = payload.get('ring_state', self.ring_state)

        if payload.get("status") == "cycle_complete":
            self.logger.info(f"Cycle for round {payload.get('round')} complete. Plots were generated by Pi {payload.get('finalizer_id')}.")
            self.current_round = payload.get('round', self.current_round) + 1
            time.sleep(INITIAL_CYCLE_DELAY)
            self._initiate_cycle_if_leader()
            return
            
        self.logger.info(f"Received data token for round {payload.get('round')}.")
        
        local_data = self._get_local_sensor_data()
        payload["data_points"].extend(local_data)
        
        initiator_id = payload['initiator_id']
        next_active_pi = self._get_next_active_pi_id(self.pi_id)
        
        # Are we the last node in the chain before the initiator?
        if next_active_pi == initiator_id:
            self.logger.info(f"I am the FINALIZER. All data collected for round {payload['round']}. Processing...")
            
            switched_to_infra = self._switch_to_infrastructure_mode()
            if switched_to_infra:
                self._upload_to_db(payload)
                switched_to_adhoc = self._switch_to_adhoc_mode()
                if not switched_to_adhoc:
                     self.logger.error("CRITICAL: FAILED to switch back to Ad-Hoc mode. Ring is broken.")
            else:
                self.logger.error("Failed to switch to Infrastructure mode. DB upload skipped.")
                
                self._switch_to_adhoc_mode()

            plot_filename = self._plot_data_and_prepare_response(payload)

            completion_payload = {
                "round": payload['round'],
                "status": "cycle_complete",
                "finalizer_id": self.pi_id,
                "message": f"Pi {self.pi_id} processed data and generated {plot_filename}."
            }
            self._client_send_to_next(completion_payload)
            
        else:
            self.logger.info("I am an AGGREGATOR. Passing token to the next node.")
            self._client_send_to_next(payload)


    def _initiate_cycle_if_leader(self):
        active_ids = self._get_active_pi_ids()
        if not active_ids:
            self.logger.warning("Attempted to start cycle, but no nodes are active (including self).")
            return
            
        leader_id = active_ids[0]

        if self.pi_id == leader_id:
            self.logger.info(f"I am the LEADER (Pi {leader_id}). Initiating new round {self.current_round}.")
            
            local_data = self._get_local_sensor_data()
            initial_payload = {
                "round": self.current_round,
                "initiator_id": self.pi_id,
                "data_points": local_data
            }
            self._client_send_to_next(initial_payload)
        else:
            self.logger.info(f"I am not the leader. The leader is Pi {leader_id}. Waiting for token.")

    def _check_for_rejoined_nodes(self):
        while True:
            time.sleep(REJOIN_CHECK_INTERVAL)
            inactive_ids = []
            with self.ring_state_lock:
                inactive_ids = [pid for pid, info in self.ring_state.items() if info["status"] == "inactive"]

            if not inactive_ids:
                continue

            self.logger.debug(f"Checking for rejoined nodes: {inactive_ids}")
            for pi_id in inactive_ids:
                ip = self.static_pi_config[pi_id]["ip"]
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                        sock.settimeout(5)
                        # Use a simple connect/disconnect to check for life
                        if sock.connect_ex((ip, self.port)) == 0:
                            with self.ring_state_lock:
                                self.ring_state[pi_id]["status"] = "active"
                            self.logger.info(f"Node {pi_id} has REJOINED the network!")
                            self._initiate_cycle_if_leader()
                except Exception as e:
                    self.logger.error(f"Error while checking rejoined node {pi_id}: {e}")

    def accept_wrapper(self, sock):
        conn, addr = sock.accept()
        self.logger.info(f"Accepted connection from {addr}")
        conn.setblocking(False)
        data = types.SimpleNamespace(addr=addr, inb=b"")
        self.sel.register(conn, selectors.EVENT_READ, data=data)

    def service_connection(self, key, mask):
        sock = key.fileobj
        data = key.data
        if mask & selectors.EVENT_READ:
            received_bytes = b""
            try:
                while True:
                    time.sleep(1)
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    received_bytes += chunk
                
                if received_bytes:
                    self._handle_ring_message(received_bytes.decode('utf-8'))
            except Exception as e:
                self.logger.error(f"Error during recv from {data.addr}: {e}")
            finally:
                self.logger.info(f"Closing connection to {data.addr}")
                self.sel.unregister(sock)
                sock.close()

    def _run_shell_command(self, command_list, check=True):
        self.logger.info(f"Executing: {' '.join(command_list)}")
        try:
            result = subprocess.run(command_list, capture_output=True, text=True, check=check, timeout=45)
            if result.stdout:
                self.logger.info(f"STDOUT: {result.stdout.strip()}")
            if result.stderr:
                self.logger.warning(f"STDERR: {result.stderr.strip()}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed: {e.cmd}, RC: {e.returncode}, ERR: {e.stderr.strip()}")
            return False
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timed out: {' '.join(command_list)}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error running command {' '.join(command_list)}: {e}")
            return False
                
    def _switch_to_infrastructure_mode(self):
        self.logger.info("Attempting to switch to INFRASTRUCTURE mode...")
        # Copy the default interfaces file
        if not self._run_shell_command(["sudo", "cp", "/etc/network/interfaces.default", "/etc/network/interfaces"]):
            self.logger.error("Failed to copy interfaces.default. Aborting switch to infrastructure.")
            return False
        time.sleep(1)

        # Enable and start NetworkManager
        if not self._run_shell_command(["sudo", "systemctl", "enable", "NetworkManager"]): 
             self.logger.warning("Could not enable NetworkManager, but proceeding to start.")
        if not self._run_shell_command(["sudo", "systemctl", "start", "NetworkManager"]):
            self.logger.error("Failed to start NetworkManager. Aborting switch to infrastructure.")
            return False
        time.sleep(10) 
        
        self.logger.info("Successfully switched to INFRASTRUCTURE mode...")
        return True
        
    def _switch_to_adhoc_mode(self):
        self.logger.info("Attempting to switch to AD-HOC mode...")

        if not self._run_shell_command(["sudo", "systemctl", "stop", "NetworkManager"]):
            self.logger.warning("Failed to stop NetworkManager, but proceeding with ad-hoc switch.")
        if not self._run_shell_command(["sudo", "systemctl", "disable", "NetworkManager"]):
            self.logger.warning("Failed to disable NetworkManager.")

        # copy the adhoc interfaces file
        if not self._run_shell_command(["sudo", "cp", "/etc/network/interfaces.adhoc", "/etc/network/interfaces"]):
            self.logger.error("Failed to copy interfaces.adhoc. Ad-hoc mode might be broken!")
            return False

        self._run_shell_command(["sudo", "ifdown", "wlan0"], check=False) 
        if not self._run_shell_command(["sudo", "ifup", "wlan0"]):
            self.logger.error("Failed to bring up wlan0 in ad-hoc mode via ifup. Critical error.")
            return False

        self.logger.info("Successfully switched to AD-HOC mode.")
        time.sleep(5) 
        return True

    def run(self):
        self.logger.debug("Starting RingNode server component...")
        # Switch to Ad-Hoc mode on startup
        if not self._switch_to_adhoc_mode():
            self.logger.critical("Could not switch to Ad-Hoc mode on startup. Aborting.")
            return

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.host_ip, self.port))
        except OSError as e:
            self.logger.error(f"Failed to bind to {self.host_ip}:{self.port} - {e}.")
            return

        self.server_socket.listen()
        self.logger.info(f"Server listening on {self.host_ip}:{self.port}")
        self.server_socket.setblocking(False)
        self.sel.register(self.server_socket, selectors.EVENT_READ, data=None)

        rejoin_thread = threading.Thread(target=self._check_for_rejoined_nodes, daemon=True)
        rejoin_thread.start()
        
        self.current_round = 1
        initial_cycle_thread = threading.Timer(INITIAL_CYCLE_DELAY, self._initiate_cycle_if_leader)
        initial_cycle_thread.start()

        try:
            while True:
                events = self.sel.select(timeout=1.0)
                for key, mask in events:
                    if key.data is None:
                        self.accept_wrapper(key.fileobj)
                    else:
                        self.service_connection(key, mask)
        except KeyboardInterrupt:
            self.logger.info("Caught keyboard interrupt, exiting.")
        finally:
            if self.server_socket:
                self.sel.unregister(self.server_socket)
                self.server_socket.close()
            self.sel.close()
            self.logger.info("RingNode server shut down.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resilient Ring Communication Node for Raspberry Pi.")
    parser.add_argument("pi_id", type=int, choices=[1, 2, 3], help="ID of this Pi node (1, 2, or 3)")
    parser.add_argument("port", type=int, help="Port to listen on")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logger_with_context = logging.LoggerAdapter(base_slogger, {'pi_id': args.pi_id})
    if args.debug:
        base_slogger.setLevel(logging.DEBUG)
    
    my_ip = PI_CONFIG.get(args.pi_id, {}).get("ip", DEFAULT_PI_IP)
    logger_with_context.info(f"Starting Node {args.pi_id} with IP {my_ip} on port {args.port}")

    node = RingNode(host_ip=my_ip,
                    port=args.port,
                    pi_id=args.pi_id,
                    ring_config=PI_CONFIG,
                    logger=logger_with_context)
    node.run()
    logger_with_context.info(f"Node {args.pi_id} has finished.")

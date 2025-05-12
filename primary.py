#!/usr/bin/env python3
#Primary. AKA the client.

import selectors
import socket
import sys
import types
import pickle
import time
import json

sel = selectors.DefaultSelector()

def start_connection(host, port):
    server_addr = (host, port)
    print(f"Starting connection to {server_addr}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    sock.connect_ex(server_addr)

    events = selectors.EVENT_READ | selectors.EVENT_WRITE
    data = types.SimpleNamespace(
        addr=server_addr,
        expected_num_bytes_recv=0,
        num_bytes_recv=0,
        inb=b"",
        outb=b"",
    )
    sel.register(sock, events, data=data)

hasNotBeenPolled = None
dataHasBeenReceived = None
sensor_data = None
def service_connection(key, mask):
    sock = key.fileobj
    data = key.data

    if mask & selectors.EVENT_READ:
        if (data.expected_num_bytes_recv == 0): # If new data, retrieve header length.
            data.expected_num_bytes_recv = int(sock.recv(4))
        
        if (recv_data := sock.recv(1024)):
            data.inb += recv_data
            data.num_bytes_recv += len(recv_data)
        else:
            # print(f"Closing connection {data.addr}")
            # sel.unregister(sock)
            # sock.close()
            # TODO
            # Add timeout check?
            # Should set dataHasBeenReceived to True, but sensor_data[data.addr] will remain None.
            pass
        
        if (data.num_bytes_recv >= data.expected_num_bytes_recv): # If all expected data has been received...
            sensor_datum = json.loads(data.inb.decode())
            # print(f"Received {sensor_datum} from addr {data.addr}")
            data.expected_num_bytes_recv = 0
            data.inb = b""
            dataHasBeenReceived[data.addr] = True
            sensor_data[data.addr] = sensor_datum
            # TODO
            # Convert from json bytes to dict. Store somewhere. Return?
    
    if mask & selectors.EVENT_WRITE: # & (hasNotBeenPolled[data.addr]):
        if not data.outb and hasNotBeenPolled[data.addr]: # (Re)Load the message.
            data.outb = b"Requesting data."
            hasNotBeenPolled[data.addr] = False
        
        if data.outb:                       # Send the message. 
            print(f"Sending {data.outb!r} to addr {data.addr}")
            num_bytes_sent = sock.send(data.outb)  # Send the data that is ready. Keep track of the # of bytes that was sent.
            data.outb = data.outb[num_bytes_sent:] # Delete the sent data. Prepare to send any yet-to-be-sent data on the next service_connection() call(s).

if ((len(sys.argv) % 2) != 1):
    print(f"# of given arguments should be a multiple of 2!")
    print(f"Usage: {sys.argv[0]} <host1> <port1> <host2> <port2> ... <hostn> <portn>")
    sys.exit(1)

num_connections = ((len(sys.argv) - 1) // 2)

server_addrs = tuple((sys.argv[2*i+1], int(sys.argv[2*i+2])) for i in range(num_connections))
conn_ids = {server_addrs[i]:i for i in range(num_connections)}
dataHasBeenReceived = {server_addrs[i]:False for i in range(num_connections)}
hasNotBeenPolled = {server_addrs[i]:True for i in range(num_connections)}
sensor_data = {server_addrs[i]:None for i in range(num_connections)}
print(f"hasNotBeenPolled = {hasNotBeenPolled}")

for i in range(num_connections):
    start_connection(sys.argv[2*i+1], int(sys.argv[2*i+2]))

try:
    while True:
        events = sel.select(timeout=1)
        # print(bool(events))
        if events:
            for key, mask in events:
                service_connection(key, mask)
                # sleep
        if not sel.get_map(): # If no open sockets registered to selector, break
            break
        
        if all(dataHasBeenReceived.values()): #(not any(hasNotBeenPolled.values())):  
            for addr in hasNotBeenPolled.keys(): # Reset polling tracker if all has been polled
                hasNotBeenPolled[addr] = True
            print(f"sensor_data = {sensor_data}")
            # TODO
            # Poll self.
            # Organize data. 
            # If none, plot. Also plot self data.
            # Set sensor_data values to none.
        time.sleep(1)
except KeyboardInterrupt:
    print("Caught keyboard interrupt, exiting")
finally:
    sel.close()
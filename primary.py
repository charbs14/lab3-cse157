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
messages = [b"Requesting data."]





sensor_data = {
    "Temperature": 1,
    "Humidity": 2,
    "Soil Moisture": 3,
    "Wind Speed": 4
}

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
        messages=messages.copy(),
        inb=b"",
        outb=b"",
    )
    sel.register(sock, events, data=data)


hasNotBeenPolled = None
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
            print(f"Closing connection {data.addr}")
            sel.unregister(sock)
            sock.close()
        
        if (data.num_bytes_recv >= data.expected_num_bytes_recv): # If all expected data has been received...
            print(f"Received {json.loads(data.inb.decode())!r} from addr {data.addr}")
            data.expected_num_bytes_recv = 0
            data.inb = b""
            # TODO
            # Convert from json bytes to dict. Store somewhere. Return?
    if mask & selectors.EVENT_WRITE: # & (hasNotBeenPolled[data.addr]):
        if not data.outb and hasNotBeenPolled[data.addr]: # (Re)Load the message.
            data.outb = b"Requesting data."
        if data.outb:                       # Send the message. 
            print(f"Sending {data.outb!r} to addr {data.addr}")
            num_bytes_sent = sock.send(data.outb)  # Send the data that is ready. Keep track of the # of bytes that was sent.
            data.outb = data.outb[num_bytes_sent:] # Delete the sent data. Prepare to send any yet-to-be-sent data on the next service_connection() call(s).
            if not data.outb:
                hasNotBeenPolled[data.addr] = False



if ((len(sys.argv) % 2) != 1):
    print(f"# of given arguments should be a multiple of 2!")
    print(f"Usage: {sys.argv[0]} <host1> <port1> <host2> <port2> ... <hostn> <portn>")
    sys.exit(1)

num_connections = ((len(sys.argv) - 1) // 2)

server_addrs = tuple((sys.argv[2*i+1], int(sys.argv[2*i+2])) for i in range(num_connections))
conn_ids = {server_addrs[i]:i for i in range(num_connections)}
hasNotBeenPolled = {server_addrs[i]:False for i in range(num_connections)}
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
        time.sleep(0.5)
        if (not any(hasNotBeenPolled.values())):  # Reset polling tracker if all has been polled
            for key in hasNotBeenPolled.keys():
                hasNotBeenPolled[key] = True
            # TODO
            # Organize data. Plot.
except KeyboardInterrupt:
    print("Caught keyboard interrupt, exiting")
finally:
    sel.close()
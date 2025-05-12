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
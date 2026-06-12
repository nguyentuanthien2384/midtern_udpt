import socket
import xmlrpc.client
import json

socket.setdefaulttimeout(2.0)

for port in [8000, 8001, 8002]:
    print(f"\n--- Checking Node {port} ---")
    try:
        p = xmlrpc.client.ServerProxy(f"http://localhost:{port}")
        info = json.loads(p.get_node_info())
        print(f"Info count: primary={info['primary_count']}, replica={info['replica_count']}")
        data = json.loads(p.get_all_data())
        print(f"Data: {data}")
    except Exception as e:
        print(f"Error checking node {port}: {e}")

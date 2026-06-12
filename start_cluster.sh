#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt

python3 node.py --node node1 --config cluster_config.json --bind-host 127.0.0.1 &
python3 node.py --node node2 --config cluster_config.json --bind-host 127.0.0.1 &
python3 node.py --node node3 --config cluster_config.json --bind-host 127.0.0.1 &
python3 manager_app.py --config cluster_config.json --host 0.0.0.0 --port 5000 &

echo "Web UI: http://localhost:5000"
echo "Nhan Ctrl+C de dung cluster."
wait

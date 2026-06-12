#!/usr/bin/env bash
# Dùng trên từng máy ảo Linux/Ubuntu.
# Ví dụ: ./run_node_vm.sh node1
set -e
cd "$(dirname "$0")"
NODE_ID="${1:-node1}"
CONFIG="${2:-cluster_config.json}"
python3 -m pip install -r requirements.txt
python3 node.py --node "$NODE_ID" --config "$CONFIG" --bind-host 0.0.0.0

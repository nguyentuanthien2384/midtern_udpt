#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
CONFIG="${1:-cluster_config.json}"
python3 -m pip install -r requirements.txt
python3 manager_app.py --config "$CONFIG" --host 0.0.0.0 --port 5000

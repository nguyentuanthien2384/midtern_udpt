#!/usr/bin/env bash
set -euo pipefail

docker compose up --build -d

echo ""
echo "✅ Docker local cluster đã chạy."
echo "   Web UI: http://localhost:5000"
echo "   Node ngoài host: node1=localhost:8001, node2=localhost:8002, node3=localhost:8003"
echo "   Test: python3 test_cluster.py --config cluster_config.docker.host.json"

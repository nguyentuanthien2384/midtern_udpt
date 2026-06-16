#!/usr/bin/env bash
set -euo pipefail
NODE="${1:-node3}"
echo "Đang tắt container $NODE ..."
docker compose stop "$NODE"
echo "✅ Đã tắt $NODE. Cluster còn lại sẽ phục vụ qua replica."
echo "   Xem trạng thái: http://localhost:5000 (mục Trạng thái Server)"

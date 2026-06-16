#!/usr/bin/env bash
set -euo pipefail
NODE="${1:-node3}"
echo "Đang bật lại container $NODE ..."
docker compose start "$NODE"
echo "✅ Đã bật $NODE. Node sẽ nạp dữ liệu từ volume rồi tự đồng bộ với cluster."
echo "   Có thể bấm 'Đồng bộ' trên Web UI để đối chiếu lại ngay nếu muốn."

#!/usr/bin/env bash
set -euo pipefail

NODE_ID_ARG="${1:-${NODE_ID:-node1}}"

if [ ! -f cluster_config.vm.docker.json ]; then
  cp cluster_config.vm.docker.example.json cluster_config.vm.docker.json
  echo "⚠️  Đã tạo cluster_config.vm.docker.json từ file mẫu. Hãy sửa IP VM trước khi chạy thật."
  echo "    Sau khi sửa xong, chạy lại: ./docker_vm_node_up.sh ${NODE_ID_ARG}"
  exit 1
fi

NODE_ID="${NODE_ID_ARG}" docker compose -f docker-compose.vm-node.yml up --build -d

echo "✅ Node ${NODE_ID_ARG} đã chạy bằng Docker."
echo "   Xem log: docker logs -f udpt-${NODE_ID_ARG}"

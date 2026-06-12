#!/usr/bin/env bash
set -euo pipefail

if [ ! -f cluster_config.vm.docker.json ]; then
  cp cluster_config.vm.docker.example.json cluster_config.vm.docker.json
  echo "⚠️  Đã tạo cluster_config.vm.docker.json từ file mẫu. Hãy sửa IP VM trước khi chạy manager."
  exit 1
fi

docker compose -f docker-compose.vm-manager.yml up --build -d

echo "✅ Manager đã chạy bằng Docker."
echo "   Web UI: http://localhost:${MANAGER_PORT:-5000}"
echo "   Xem log: docker logs -f udpt-manager"

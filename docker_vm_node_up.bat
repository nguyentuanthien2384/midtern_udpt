@echo off
set NODE_TO_RUN=%1
if "%NODE_TO_RUN%"=="" set NODE_TO_RUN=node1

if not exist cluster_config.vm.docker.json (
  copy cluster_config.vm.docker.example.json cluster_config.vm.docker.json
  echo Da tao cluster_config.vm.docker.json tu file mau. Hay sua IP VM truoc khi chay lai.
  pause
  exit /b 1
)

set NODE_ID=%NODE_TO_RUN%
docker compose -f docker-compose.vm-node.yml up --build -d
echo Node %NODE_TO_RUN% da chay bang Docker.
echo Xem log: docker logs -f udpt-%NODE_TO_RUN%
pause

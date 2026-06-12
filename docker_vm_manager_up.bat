@echo off
if not exist cluster_config.vm.docker.json (
  copy cluster_config.vm.docker.example.json cluster_config.vm.docker.json
  echo Da tao cluster_config.vm.docker.json tu file mau. Hay sua IP VM truoc khi chay manager.
  pause
  exit /b 1
)

docker compose -f docker-compose.vm-manager.yml up --build -d
echo Manager da chay bang Docker.
echo Web UI: http://localhost:5000
pause

@echo off
docker compose up --build -d
echo.
echo Docker local cluster da chay.
echo Web UI: http://localhost:5000
echo Test: python test_cluster.py --config cluster_config.docker.host.json
pause

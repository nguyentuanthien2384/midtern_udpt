@echo off
setlocal

cd /d "%~dp0"

echo Starting KV cluster from: %CD%
echo.

start "KV Manager" cmd /k "cd /d ""%~dp0"" && python manager_app.py --config cluster_config.json --host 0.0.0.0 --port 5000"
start "KV Node1" cmd /k "cd /d ""%~dp0"" && python node.py --node node1 --config cluster_config.json --bind-host 127.0.0.1"
start "KV Node2" cmd /k "cd /d ""%~dp0"" && python node.py --node node2 --config cluster_config.json --bind-host 127.0.0.1"
start "KV Node3" cmd /k "cd /d ""%~dp0"" && python node.py --node node3 --config cluster_config.json --bind-host 127.0.0.1"

timeout /t 2 >nul
start "" "http://127.0.0.1:5000"

echo Done. 4 windows launched: Manager + Node1 + Node2 + Node3
echo Web UI: http://127.0.0.1:5000
endlocal

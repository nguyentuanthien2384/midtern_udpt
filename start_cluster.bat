@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   ARS KV Store - Local Cluster tren Windows
echo ============================================
echo.

echo [1/5] Cai thu vien Python neu can...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Loi cai requirements. Hay kiem tra Python/pip.
  pause
  exit /b 1
)

echo [2/5] Khoi dong node1...
start "node1" cmd /k "chcp 65001 ^>nul && python node.py --node node1 --config cluster_config.json --bind-host 127.0.0.1"
timeout /t 2 >nul

echo [3/5] Khoi dong node2...
start "node2" cmd /k "chcp 65001 ^>nul && python node.py --node node2 --config cluster_config.json --bind-host 127.0.0.1"
timeout /t 2 >nul

echo [4/5] Khoi dong node3...
start "node3" cmd /k "chcp 65001 ^>nul && python node.py --node node3 --config cluster_config.json --bind-host 127.0.0.1"
timeout /t 2 >nul

echo [5/5] Khoi dong Web Management...
start "manager" cmd /k "chcp 65001 ^>nul && python manager_app.py --config cluster_config.json --host 0.0.0.0 --port 5000"
timeout /t 2 >nul

echo.
echo ============================================
echo   Da khoi dong xong!
echo   Web UI: http://localhost:5000
echo ============================================
start http://localhost:5000

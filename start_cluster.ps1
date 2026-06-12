# Chạy local cluster trên Windows PowerShell
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 | Out-Null
Write-Host "============================================"
Write-Host "  ARS KV Store - Local Cluster tren Windows"
Write-Host "============================================"

python -m pip install -r requirements.txt

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; python node.py --node node1 --config cluster_config.json --bind-host 127.0.0.1"
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; python node.py --node node2 --config cluster_config.json --bind-host 127.0.0.1"
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; python node.py --node node3 --config cluster_config.json --bind-host 127.0.0.1"
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; python manager_app.py --config cluster_config.json --host 0.0.0.0 --port 5000"
Start-Sleep -Seconds 2
Start-Process "http://localhost:5000"

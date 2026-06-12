@echo off
chcp 65001 >nul
cd /d "%~dp0"
set CONFIG=%1
if "%CONFIG%"=="" set CONFIG=cluster_config.json
python -m pip install -r requirements.txt
python manager_app.py --config %CONFIG% --host 0.0.0.0 --port 5000

@echo off
chcp 65001 >nul
cd /d "%~dp0"
set NODE_ID=%1
if "%NODE_ID%"=="" set NODE_ID=node1
set CONFIG=%2
if "%CONFIG%"=="" set CONFIG=cluster_config.json
python -m pip install -r requirements.txt
python node.py --node %NODE_ID% --config %CONFIG% --bind-host 0.0.0.0

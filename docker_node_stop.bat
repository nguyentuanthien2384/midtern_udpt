@echo off
set NODE=%1
if "%NODE%"=="" set NODE=node3
echo Dang tat container %NODE% ...
docker compose stop %NODE%
echo Da tat %NODE%. Cluster con lai se phuc vu qua replica.

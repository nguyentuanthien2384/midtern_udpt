@echo off
set NODE=%1
if "%NODE%"=="" set NODE=node3
echo Dang bat lai container %NODE% ...
docker compose start %NODE%
echo Da bat %NODE%. Node se nap du lieu tu volume roi tu dong bo.

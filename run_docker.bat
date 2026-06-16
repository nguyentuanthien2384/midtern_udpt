@echo off
setlocal

cd /d "%~dp0"
set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

echo =========================================
echo  UDPT Docker quick start
echo  Folder: %CD%
echo =========================================
echo.

where docker >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker CLI not found. Please install/open Docker Desktop first.
  if "%NO_PAUSE%"=="0" pause
  exit /b 1
)

echo [1/3] Build + start services...
docker compose up --build -d
if errorlevel 1 (
  echo.
  echo [ERROR] docker compose up failed.
  echo Please check Docker Desktop and retry.
  if "%NO_PAUSE%"=="0" pause
  exit /b 1
)

echo.
echo [2/3] Service status:
docker compose ps

echo.
echo [3/3] Opening Web UI...
ping 127.0.0.1 -n 3 >nul
start "" "http://localhost:5000"

echo.
echo Done.
echo Web UI: http://localhost:5000
echo Stop cluster later with: docker compose down
echo.
if "%NO_PAUSE%"=="0" pause
endlocal

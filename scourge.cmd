@echo off
rem Scourge on Windows: double-click to start the demo, or run "scourge test" / "scourge stop".
rem Needs only Docker Desktop (https://www.docker.com/products/docker-desktop/).
cd /d "%~dp0"
where docker >nul 2>nul || (echo Docker Desktop is not installed. Get it from https://www.docker.com/products/docker-desktop/ & pause & exit /b 1)
docker info >nul 2>nul || (echo Docker Desktop is not running. Start it, wait for "Engine running", then try again. & pause & exit /b 1)
if /i "%~1"=="stop" (docker compose down & exit /b 0)
if /i "%~1"=="test" (
  docker compose --profile ops build tests && docker compose --profile ops run --rm tests
  pause & exit /b
)
echo Building and starting Scourge (first time takes a few minutes)...
docker compose up -d --build || (pause & exit /b 1)
set /a n=0
:wait
curl -sf http://localhost:8000/api/health >nul 2>nul && goto up
set /a n+=1
if %n% geq 90 (echo Scourge did not start. Run: docker compose logs & pause & exit /b 1)
timeout /t 2 /nobreak >nul
goto wait
:up
echo Scourge is running at http://localhost:8000   (stop it with: scourge stop)
start "" http://localhost:8000

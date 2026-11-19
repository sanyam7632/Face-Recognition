@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Face Recognition Launcher

rem Always run from the folder containing this launcher.
cd /d "%~dp0"

echo.
echo ========================================
echo          Face Recognition Launcher
echo ========================================
echo.

set "docker_cmd=docker"
where docker >nul 2>&1
if errorlevel 1 (
    if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" (
        set "docker_cmd=%ProgramFiles%\Docker\Docker\resources\bin\docker.exe"
    ) else (
        echo [ERROR] Docker is not installed.
        echo Install Docker Desktop, restart Windows, and run this file again.
        echo.
        pause
        exit /b 1
    )
)

where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js and npm are not installed or are not available in PATH.
    echo Install the current Node.js LTS release and run this file again.
    echo.
    pause
    exit /b 1
)

"!docker_cmd!" info >nul 2>&1
if not errorlevel 1 goto docker_ready

if not exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" goto docker_not_found
echo Starting Docker Desktop...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a docker_tries=0

:wait_for_docker
timeout /t 3 /nobreak >nul
"!docker_cmd!" info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a docker_tries+=1
if !docker_tries! GEQ 40 goto docker_timeout
goto wait_for_docker

:docker_ready
echo Docker is ready.

if not exist ".env" (
    echo Creating a local configuration file...
    copy /y ".env.example" ".env" >nul
    for /f %%S in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString() + [guid]::NewGuid().ToString()"') do set "face_recognition_secret=%%S"
    powershell -NoProfile -Command "$p='.env'; $v=Get-Content -LiteralPath $p -Raw; $v=$v.Replace('replace-with-a-long-random-value','!face_recognition_secret!'); Set-Content -LiteralPath $p -Value $v -NoNewline"
    set "face_recognition_secret="
)

echo.
echo Building and starting the Face Recognition backend...
"!docker_cmd!" compose up --build -d
if errorlevel 1 (
    echo.
    echo [ERROR] The backend could not be started.
    echo Review the Docker message above, then run this file again.
    echo.
    pause
    exit /b 1
)

echo Backend started at http://localhost:8000
echo.
echo Preparing the laptop web application...
pushd "frontend"
call npm install --prefer-offline --loglevel warn
if errorlevel 1 (
    popd
    echo.
    echo [ERROR] Frontend dependencies could not be installed.
    echo Check your internet connection and run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting Face Recognition in the browser...
echo Keep this window open while using the laptop camera.
echo Press Ctrl+C when you want to stop the web interface.
echo.
call npm run web
set "web_exit=%errorlevel%"
popd

echo.
echo Face Recognition web interface stopped. The Docker backend is still running.
echo To stop it later, run: docker compose down
echo.
if not "%web_exit%"=="0" pause
exit /b %web_exit%

:docker_timeout
echo.
echo [ERROR] Docker Desktop did not become ready within two minutes.
echo Check that hardware virtualization is enabled, then try again.
echo.
pause
exit /b 1

:docker_not_found
echo [ERROR] Docker Desktop is not running and its application was not found.
echo Start Docker Desktop manually, then run this file again.
echo.
pause
exit /b 1

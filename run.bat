@echo off
rem ============================================================
rem  Keymento launcher  --  keep this file ASCII-only.
rem  cmd.exe parses .bat with the active codepage, so non-ASCII
rem  text here breaks parsing before chcp can take effect.
rem  Korean help is printed by Python (see :help below).
rem ============================================================
setlocal
cd /d "%~dp0"

rem UTF-8 console: Python prints emoji, which dies on cp949.
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set PY=venv\Scripts\python.exe
if not exist "%PY%" (
    echo [!] venv not found: %CD%\%PY%
    echo.
    echo     python -m venv venv
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    exit /b 1
)

set MAIN=src\piano-ar\main.py

if /i "%~1"=="nocam"    goto :shortcut
if /i "%~1"=="demo"     goto :shortcut
if /i "%~1"=="selftest" goto :shortcut
if /i "%~1"=="ports"    goto :shortcut
if /i "%~1"=="base"     goto :shortcut
if /i "%~1"=="help"     goto :shortcut
if /i "%~1"=="/?"       goto :shortcut
goto :passthrough

rem Collect everything after the shortcut word so extra flags still reach
rem main.py -- e.g. "run base --midi-port 2".
:shortcut
set "WORD=%~1"
shift
set "REST="
:collect
if "%~1"=="" goto :dispatch
set "REST=%REST% %1"
shift
goto :collect

:dispatch
if /i "%WORD%"=="nocam"    goto :nocam
if /i "%WORD%"=="demo"     goto :demo
if /i "%WORD%"=="selftest" goto :selftest
if /i "%WORD%"=="ports"    goto :ports
if /i "%WORD%"=="base"     goto :base
goto :help

:nocam
"%PY%" %MAIN% --no-camera%REST%
goto :end

:demo
"%PY%" %MAIN% --demo%REST%
goto :end

:selftest
"%PY%" %MAIN% --demo --no-camera%REST%
goto :end

:ports
"%PY%" %MAIN% --list-midi%REST%
goto :end

:base
"%PY%" %MAIN% --detect-base%REST%
goto :end

:help
"%PY%" %MAIN% --guide
goto :end

:passthrough
"%PY%" %MAIN% %*

:end
endlocal
